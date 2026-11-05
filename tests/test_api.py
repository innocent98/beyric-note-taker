"""The JSON API under /api/: same session, same rules as the pages, one error shape.

Fixtures (app, fakes, template) come from test_web.py so both suites drive the same wiring.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth
from app.db import get_session
from app.models import AppState, Decision, Meeting, ReviewItem, Template, VocabularyTerm
from app.pipeline import REASON_CANCELLED, REASON_JOIN_REFUSED, REASON_NO_SPEECH
from app.services import RETRY_BLOCK_TEXT
from app.vexa import VexaError
from tests.test_web import (  # noqa: F401  (fixtures used by name)
    MEET,
    NOTES,
    OTHER_EMAIL,
    OWNER_EMAIL,
    PASSWORD,
    TRANSCRIPT,
    ZOOM,
    FakeOpenAI,
    add_meeting,
    anon_client,
    app,
    client,
    fake_openai,
    fake_vexa,
    login_data,
    make_user,
    owner,
    stranger,
    template,
)

CSRF = auth.CSRF_HEADER


class Api:
    """A JSON client that remembers the CSRF token and sends it on writes."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.csrf = client.get("/api/session").json()["csrf_token"]

    def call(self, method: str, url: str, json=None, csrf: str | None | bool = True):
        token = self.csrf if csrf is True else csrf
        headers = {CSRF: token} if token else {}
        response = self.client.request(method, url, json=json, headers=headers)
        if url == "/api/session" and response.status_code == 200:
            self.csrf = response.json()["csrf_token"]  # a new session has a new token
        return response

    def get(self, url):
        return self.call("GET", url, csrf=False)

    def post(self, url, json=None, **kw):
        return self.call("POST", url, json, **kw)

    def put(self, url, json=None, **kw):
        return self.call("PUT", url, json, **kw)

    def delete(self, url, **kw):
        return self.call("DELETE", url, **kw)

    def log_in(self, password=PASSWORD, email=OWNER_EMAIL):
        return self.post("/api/session", {"email": email, "password": password})


def ready(session, template, **fields) -> Meeting:
    values = {"status": "ready", "step": "done", "transcript": TRANSCRIPT, "notes": NOTES}
    return add_meeting(session, template, **{**values, "speaker_transcript": TRANSCRIPT, **fields})


@pytest.fixture
def anon(anon_client):
    return Api(anon_client)


@pytest.fixture
def api(anon, owner):
    assert anon.log_in().status_code == 200
    return anon


def error_of(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body["error"]


# ---- session ----


def test_a_visitor_gets_a_session_and_a_csrf_token(anon_client):
    response = anon_client.get("/api/session")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is False
    assert len(body["csrf_token"]) >= 32
    assert "session" in response.cookies
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    # The cookie is kept, so the same token comes back.
    assert anon_client.get("/api/session").json() == body


def test_login_returns_a_fresh_csrf_token(anon, owner):
    before = anon.csrf
    response = anon.log_in()
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["csrf_token"] != before
    assert anon.client.get("/api/session").json() == body


def test_login_with_the_old_token_after_login_is_refused(api):
    stale = auth.new_session()["csrf"]
    error_of(
        api.put("/api/templates/1", {"name": "a", "instructions": "b"}, csrf=stale),
        403,
        "csrf_rejected",
    )


def test_wrong_password_is_401_and_does_not_log_in(anon, owner):
    error = error_of(anon.log_in("nope"), 401, "invalid_credentials")
    assert error["message"] == "That email or password is not right."
    assert anon.client.get("/api/session").json()["authenticated"] is False


@pytest.mark.parametrize("password", ["", "pässwörd ✓", "x" * 10_000])
def test_odd_passwords_are_ordinary_failures(anon, owner, password):
    error_of(anon.log_in(password), 401, "invalid_credentials")


def test_login_needs_the_csrf_header(anon, owner):
    body = {"email": OWNER_EMAIL, "password": PASSWORD}
    error_of(anon.post("/api/session", body, csrf=False), 403, "csrf_rejected")
    error_of(anon.post("/api/session", body, csrf="wrong"), 403, "csrf_rejected")
    assert anon.client.get("/api/session").json()["authenticated"] is False


def test_login_is_rate_limited_and_shares_the_pages_limit(anon, owner):
    for _ in range(5):
        error_of(anon.log_in("nope"), 401, "invalid_credentials")
    error = error_of(anon.log_in("nope"), 429, "too_many_attempts")
    assert error["message"] == "Too many failed attempts. Try again later."
    error_of(anon.log_in(), 429, "too_many_attempts")  # even the right password waits
    # The login page counts the same client.
    token = re.search(r'name="csrf_token" value="([^"]+)"', anon.client.get("/login").text)[1]
    page = anon.client.post("/login", data=login_data(token))
    assert page.status_code == 429


def test_a_good_login_gives_back_its_own_attempt_not_the_earlier_failures(anon, owner):
    for _ in range(4):
        anon.log_in("nope")
    assert anon.log_in().status_code == 200  # counted, then given back: four failures remain
    anon.delete("/api/session")
    error_of(anon.log_in("nope"), 401, "invalid_credentials")  # the fifth failure is still allowed
    error_of(anon.log_in("nope"), 429, "too_many_attempts")


def test_login_body_must_be_valid(anon):
    error = error_of(anon.post("/api/session", {}), 422, "validation_error")
    assert error["fields"] == [
        {"field": "email", "message": "Field required"},
        {"field": "password", "message": "Field required"},
    ]
    error = error_of(
        anon.post("/api/session", {"email": "a@b.test", "password": 123456}),
        422,
        "validation_error",
    )
    assert error["fields"][0]["field"] == "password"
    assert "123456" not in json.dumps(error)  # submitted values are never echoed
    error_of(
        anon.post("/api/session", {"email": "a@b.test", "password": "x", "extra": 1}),
        422,
        "validation_error",
    )
    error_of(anon.post("/api/session", {"password": "x"}), 422, "validation_error")  # no email


def test_logout_revokes_every_copy_of_the_cookie(anon, app, owner):
    anon.log_in()
    cookie, before = anon.client.cookies.get("session"), anon.csrf
    response = anon.delete("/api/session")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is False and body["csrf_token"] != before
    with TestClient(app, follow_redirects=False, cookies={"session": cookie}) as replay:
        error_of(replay.get("/api/templates"), 401, "not_authenticated")


def test_logout_needs_login_and_the_header(anon, owner):
    error_of(anon.delete("/api/session"), 401, "not_authenticated")
    anon.log_in()
    error_of(anon.delete("/api/session", csrf=False), 403, "csrf_rejected")
    assert anon.client.get("/api/session").json()["authenticated"] is True


def test_one_cookie_serves_the_pages_and_the_api(anon, owner):
    anon.log_in()
    assert anon.client.get("/templates").status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]+)"', anon.client.get("/templates").text)[1]
    assert token == anon.csrf  # the form token and the API token are the same session token
    anon.client.post("/logout", data={"csrf_token": token})
    error_of(anon.client.get("/api/templates"), 401, "not_authenticated")


def test_an_expired_session_is_401_json_never_a_redirect(anon, app):
    key = app.state.session_key
    old = auth.new_session(uid=1, now=1_000_000)
    with TestClient(app, follow_redirects=False) as client:
        client.cookies.set("session", auth.sign_session(key, old))
        error_of(client.get("/api/meetings"), 401, "not_authenticated")


# ---- every route is protected ----


def api_routes(app):
    """Every (method, path) the app serves under /api/, read from its own schema."""
    paths = app.openapi()["paths"]  # the method works although the /openapi.json URL is off
    routes = [
        (method.upper(), path)
        for path, methods in paths.items()
        if path.startswith("/api/")
        for method in methods
    ]
    assert len(routes) >= 15  # fails loudly if the router stops being mounted
    return sorted(routes)


def concrete(path: str) -> str:
    return re.sub(r"\{\w+\}", "1", path)


PUBLIC = {
    ("GET", "/api/session"),
    ("POST", "/api/session"),
    ("GET", "/api/pilot"),
    ("POST", "/api/signup"),
    ("POST", "/api/signup/resend"),
    ("POST", "/api/verify-email"),
    ("POST", "/api/password/forgot"),
    ("POST", "/api/password/reset"),
    ("POST", "/api/waitlist"),
    ("GET", "/api/auth/google/start"),
    ("GET", "/api/auth/google/callback"),
    ("GET", "/api/calendar/google/callback"),
}


def test_every_api_route_needs_login_except_the_listed_public_ones(anon, app):
    for method, path in api_routes(app):
        response = anon.call(method, concrete(path), csrf=False)
        if (method, path) in PUBLIC:
            assert response.status_code != 401, (method, path)
        else:
            error_of(response, 401, "not_authenticated")
            # with a valid token and a body too: the login check is not skipped
            response = anon.call(method, concrete(path), {"x": 1})
            error_of(response, 401, "not_authenticated")


# Routes that answer with a file, not JSON. The enumeration above reaches them too (a missing
# meeting is a 404, an anonymous caller a 401); this names them so one cannot be left out of it.
FILE_ROUTES = {("GET", "/api/meetings/{meeting_id}/export")}


def test_the_routes_that_return_files_are_listed_and_not_public(app):
    routes = set(api_routes(app))
    assert FILE_ROUTES <= routes
    assert not FILE_ROUTES & PUBLIC


def test_every_write_needs_the_csrf_header(api, app, template):
    for method, path in api_routes(app):
        if method == "GET":
            continue
        for token in (False, "wrong-token"):
            response = api.call(method, concrete(path), {"x": 1}, csrf=token)
            error_of(response, 403, "csrf_rejected")


def test_reads_work_without_the_header_and_no_route_is_cross_wired(api, app, template):
    for method, path in api_routes(app):
        if method == "GET":
            # an ordinary user is refused the admin routes (tests/test_admin.py covers each)
            allowed = (403,) if path.startswith("/api/admin/") else (200, 404)
            assert api.get(concrete(path)).status_code in allowed


def test_malformed_json_is_422_before_the_login_check_and_leaks_nothing(anon):
    """FastAPI decodes a JSON body before it runs dependencies, so this one case answers 422
    where a well-formed body gets 401. Nothing is read, changed or echoed."""
    response = anon.client.post(
        "/api/meetings",
        content=b"{not json",
        headers={CSRF: anon.csrf, "content-type": "application/json"},
    )
    error = error_of(response, 422, "validation_error")
    assert error["fields"] == [{"field": "body", "message": "JSON decode error"}]
    assert "{not json" not in response.text


@pytest.mark.parametrize(
    "host", ["x/api/session?", "x/health?", "x/login#", "evil.test/static/x?", "x\\api/session"]
)
def test_an_odd_host_header_cannot_change_which_path_is_guarded(anon, host):
    """The guard decides from the matched path, never from the Host header."""
    with TestClient(anon.client.app, follow_redirects=False) as visitor:
        response = visitor.get("/api/meetings", headers={"Host": host})
        error_of(response, 401, "not_authenticated")
        page = visitor.get("/templates", headers={"Host": host})
    assert page.status_code == 303 and page.headers["location"] == "/login"
    response = anon.client.post("/api/vocabulary", json={"term": "x"}, headers={"Host": host})
    assert response.status_code == 401  # no session: refused before anything is checked


# ---- error shape ----


def test_unknown_api_paths_and_methods_use_the_error_shape(api):
    error_of(api.get("/api/nope"), 404, "not_found")
    error_of(api.get("/api/meetings/abc"), 404, "not_found")
    error_of(api.get("/api/meetings/99999999999"), 404, "not_found")  # 11 digits: no route
    response = api.call("PATCH", "/api/templates")
    error_of(response, 405, "method_not_allowed")
    assert "allow" in response.headers


def test_the_pages_keep_their_own_error_handling(client, app):
    assert client.get("/nope").json() == {"detail": "Not Found"}
    with TestClient(app, follow_redirects=False) as visitor:
        response = visitor.get("/templates")
    assert response.status_code == 303 and response.headers["location"] == "/login"
    forbidden = client.post("/templates", data={"name": "a", "instructions": "b"})
    assert forbidden.status_code == 403 and "expired" in forbidden.text


def test_a_crash_is_a_json_500_with_no_detail(app):
    def boom():
        raise RuntimeError("secret internals")
        yield

    app.dependency_overrides[get_session] = boom
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/session")  # public, but guard needs the database
    error = error_of(response, 500, "server_error")
    assert "secret" not in json.dumps(error)


def test_openapi_and_docs_stay_off_in_the_app(api):
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert api.client.get(path).status_code == 404


def _outline(schema: dict) -> dict:
    """What the frontend depends on: operations, and each model's field names and requirements.

    Not the whole file: its wording shifts with FastAPI and pydantic releases, which is no reason
    to fail a build. `python scripts/export_openapi.py --check` compares everything.
    """
    operations = {
        (path, method): op["operationId"]
        for path, item in schema["paths"].items()
        for method, op in item.items()
    }
    models = {
        name: (sorted(model.get("properties", {})), sorted(model.get("required", [])))
        for name, model in schema["components"]["schemas"].items()
    }
    return {"operations": operations, "models": models}


def test_the_committed_openapi_file_matches_the_routes():
    from scripts.export_openapi import build

    committed = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
    assert _outline(json.loads(committed.read_text())) == _outline(build()), (
        "the API changed: run `python scripts/export_openapi.py` and commit docs/openapi.json"
    )


def test_a_login_through_the_page_works_for_the_api(client):
    body = client.get("/api/session").json()
    assert body["authenticated"] is True
    assert client.get("/api/templates").status_code == 200


# ---- meetings: reading ----


def test_the_list_is_empty_and_flags_a_silent_worker(api):
    body = api.get("/api/meetings").json()
    assert body == {
        "meetings": [],
        "has_more": False,
        "next_before_id": None,
        "worker_stale": True,
        "refresh_after_seconds": None,
    }


def test_worker_stale_follows_the_heartbeat(api, session):
    session.add(AppState(id=1, worker_heartbeat_at=datetime.now(UTC) - timedelta(minutes=19)))
    session.commit()
    assert api.get("/api/meetings").json()["worker_stale"] is False
    state = session.get(AppState, 1)
    state.worker_heartbeat_at = datetime.now(UTC) - timedelta(minutes=21)
    session.commit()
    assert api.get("/api/meetings").json()["worker_stale"] is True


def test_list_items_are_summaries_newest_first(api, session, template):
    first = add_meeting(session, template, native_id="aaa-aaaa-aaa")
    second = add_meeting(
        session,
        template,
        platform="zoom",
        native_id="12345678901",
        url=ZOOM,
        status="processing",
        step="audio",
    )
    body = api.get("/api/meetings").json()
    assert [m["id"] for m in body["meetings"]] == [second.id, first.id]
    item = body["meetings"][0]
    assert item == {
        "id": second.id,
        "title": "Zoom 12345678901",
        "platform": "zoom",
        "native_id": "12345678901",
        "status": "processing",
        "step": "audio",
        "status_text": "Downloading the audio",
        "status_tone": "info",
        "template_id": template.id,
        "template_name": "General",
        "created_at": item["created_at"],
    }
    assert item["created_at"].endswith("Z")
    assert body["meetings"][1]["title"] == "Google Meet aaa-aaaa-aaa"


def test_list_pages_with_limit_and_before_id(api, session, template):
    ids = [add_meeting(session, template).id for _ in range(5)]
    page = api.get("/api/meetings?limit=2").json()
    assert [m["id"] for m in page["meetings"]] == [ids[4], ids[3]]
    assert page["has_more"] is True and page["next_before_id"] == ids[3]
    page = api.get(f"/api/meetings?limit=2&before_id={ids[3]}").json()
    assert [m["id"] for m in page["meetings"]] == [ids[2], ids[1]]
    last = api.get(f"/api/meetings?limit=2&before_id={ids[1]}").json()
    assert [m["id"] for m in last["meetings"]] == [ids[0]]
    assert last["has_more"] is False and last["next_before_id"] is None


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=101", "limit=x", "before_id=0", "before_id=99999999999"]
)
def test_list_query_is_validated(api, query):
    error = error_of(api.get(f"/api/meetings?{query}"), 422, "validation_error")
    assert error["fields"][0]["field"] == query.split("=")[0]


def test_the_list_holds_at_most_the_default_page_when_there_are_many(api, session, template):
    for _ in range(52):
        session.add(
            Meeting(
                owner_user_id=template.owner_user_id,
                url=MEET,
                platform="google_meet",
                native_id="x",
                template_id=template.id,
            )
        )
    session.commit()
    body = api.get("/api/meetings").json()
    assert len(body["meetings"]) == 50 and body["has_more"] is True


def test_detail_of_a_meeting_waiting_for_its_bot(api, session, template):
    meeting = add_meeting(session, template, join_attempts=2)
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["status"] == "bot_sent" and body["status_text"] == "Bot sent"
    assert body["status_tone"] == "warning"
    assert body["can_cancel"] is True and body["can_retry"] is False
    assert body["can_regenerate"] is False and body["can_delete"] is False
    assert body["refresh_after_seconds"] == 30
    assert body["join_attempts"] == 2 and body["max_join_attempts"] == 3
    assert body["failure_reason"] is None and body["retry_block"] is None
    assert body["notes"] is None and body["notes_html"] is None and body["transcript"] is None


@pytest.mark.parametrize(
    ("step", "text"),
    [
        ("waiting", "Getting started"),
        ("audio", "Downloading the audio"),
        ("transcribing", "Writing the transcript"),
        ("speakers", "Working out who said what"),
        ("notes", "Writing the notes"),
    ],
)
def test_detail_of_a_processing_meeting_names_the_step(api, session, template, step, text):
    meeting = add_meeting(session, template, status="processing", step=step)
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["step"] == step and body["status_text"] == text and body["status_tone"] == "info"
    assert body["refresh_after_seconds"] == 30 and body["can_cancel"] is False


def test_detail_of_a_ready_meeting_has_notes_and_transcript(api, session, template):
    meeting = ready(session, template, speakers_incomplete=True)
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["status_text"] == "Ready" and body["status_tone"] == "success"
    assert body["notes"] == NOTES
    assert "<h2>Summary</h2>" in body["notes_html"]
    assert body["transcript"] == TRANSCRIPT and body["speakers_incomplete"] is True
    assert body["can_regenerate"] is True and body["can_delete"] is True
    assert body["refresh_after_seconds"] is None
    assert body["bot_left_early"] is False


def test_detail_has_duration_participants_and_the_two_counts(api, session, template, owner):
    spoken = "[Ada]: hi\n[Bo]: hello\n[Ada]: again\ncontinued\n[Cy]: bye"
    meeting = ready(session, template, speaker_transcript=spoken, recorded_seconds=2760)
    session.add_all(
        [
            Decision(meeting_id=meeting.id, text="a", position=0, fingerprint="a"),
            Decision(meeting_id=meeting.id, text="b", position=1, fingerprint="b"),
            Decision(
                meeting_id=meeting.id,
                text="c",
                position=2,
                fingerprint="c",
                hidden_at=datetime.now(UTC),
            ),
            ReviewItem(
                meeting_id=meeting.id, owner_user_id=owner.id, text="r", position=0, fingerprint="r"
            ),
            ReviewItem(
                meeting_id=meeting.id,
                owner_user_id=owner.id,
                text="s",
                position=1,
                fingerprint="s",
                checked_at=datetime.now(UTC),
            ),
        ]
    )
    session.commit()
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["duration_seconds"] == 2760
    assert body["participants"] == ["Ada", "Bo", "Cy"]
    assert body["decisions_count"] == 2 and body["review_open_count"] == 1


def test_detail_without_speakers_or_a_recording_has_empty_values(api, session, template):
    meeting = ready(session, template, transcript="plain", speaker_transcript=None)
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["duration_seconds"] is None and body["participants"] == []
    assert body["decisions_count"] == 0 and body["review_open_count"] == 0
    waiting = add_meeting(session, template)
    body = api.get(f"/api/meetings/{waiting.id}").json()
    assert body["decisions_count"] == 0 and body["participants"] == []


def test_transcript_prefers_the_speaker_version(api, session, template):
    meeting = ready(session, template, transcript="plain text", speaker_transcript="[Ada]: hi")
    assert api.get(f"/api/meetings/{meeting.id}").json()["transcript"] == "[Ada]: hi"
    meeting = ready(session, template, transcript="plain text", speaker_transcript=None)
    assert api.get(f"/api/meetings/{meeting.id}").json()["transcript"] == "plain text"


def test_bot_left_early_only_for_removal_reasons_on_ready_meetings(api, session, template):
    evicted = ready(session, template, completion_reason="evicted")
    assert api.get(f"/api/meetings/{evicted.id}").json()["bot_left_early"] is True
    normal = ready(session, template, completion_reason="completed")
    assert api.get(f"/api/meetings/{normal.id}").json()["bot_left_early"] is False


def test_notes_html_is_escaped_and_drops_script_links(api, session, template):
    notes = "## Summary\n<script>alert(1)</script> [bad](javascript:alert(1)) ![i](http://x/y.png)"
    meeting = ready(session, template, notes=notes)
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["notes"] == notes  # raw markdown untouched
    html = body["notes_html"]
    assert "<script>" in notes and "<script>" not in html and "&lt;script&gt;" in html
    assert 'href="javascript' not in html  # the link is not rendered at all
    assert "<img" not in html  # images are off: nothing loads from a remote host


def test_notes_html_links_are_nofollow(api, session, template):
    meeting = ready(session, template, notes="[ok](https://example.com)")
    html = api.get(f"/api/meetings/{meeting.id}").json()["notes_html"]
    assert 'rel="noopener noreferrer nofollow"' in html


def test_the_meeting_link_is_never_returned(api, session, template):
    meeting = add_meeting(session, template, platform="zoom", native_id="12345678901", url=ZOOM)
    for url in ("/api/meetings", f"/api/meetings/{meeting.id}"):
        assert "SECRETPWD" not in api.get(url).text and "zoom.us" not in api.get(url).text


@pytest.mark.parametrize(
    ("fields", "block"),
    [
        ({}, None),
        ({"step": "waiting"}, "no_audio"),
        ({"failure_reason": REASON_NO_SPEECH, "step": "transcribing"}, "no_speech"),
        ({"step": "transcribing", "audio_deleted_at": datetime.now(UTC)}, "audio_deleted"),
        ({"step": "transcribing", "audio_deleted_at": datetime.now(UTC), "transcript": "t"}, None),
    ],
)
def test_detail_of_a_failed_meeting_says_whether_it_can_be_retried(
    api, session, template, fields, block
):
    values = {"status": "failed", "step": "audio", "failure_reason": "The recording failed."}
    meeting = add_meeting(session, template, **{**values, **fields})
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["status_text"] == "Failed" and body["status_tone"] == "danger"
    assert body["retry_block"] == block and body["can_retry"] is (block is None)
    assert body["retry_block_message"] == RETRY_BLOCK_TEXT.get(block)
    assert body["failure_reason"] == fields.get("failure_reason", "The recording failed.")
    assert body["refresh_after_seconds"] is None


def test_a_failed_meeting_without_a_reason_gets_the_pages_fallback(api, session, template):
    meeting = add_meeting(session, template, status="failed", step="audio", failure_reason=None)
    reason = api.get(f"/api/meetings/{meeting.id}").json()["failure_reason"]
    assert reason == "Something went wrong while processing this meeting."


def test_retry_block_messages_are_the_ones_the_page_shows(client, session, template):
    for block, fields in (
        ("no_audio", {"step": "waiting"}),
        ("no_speech", {"failure_reason": REASON_NO_SPEECH, "step": "audio"}),
        ("audio_deleted", {"step": "audio", "audio_deleted_at": datetime.now(UTC)}),
    ):
        meeting = add_meeting(session, template, status="failed", **fields)
        assert RETRY_BLOCK_TEXT[block] in client.get(f"/meetings/{meeting.id}").text


def test_refused_meeting_detail_shows_the_wait_in_the_existing_fields(api, session, template):
    refused = add_meeting(
        session,
        template,
        status="failed",
        failure_reason=REASON_JOIN_REFUSED,
        completion_reason="google_meet_join_refused",
        terminal_seen_at=datetime.now(UTC) - timedelta(minutes=1),
        join_attempts=2,
    )
    body = api.get(f"/api/meetings/{refused.id}").json()
    assert body["failure_reason"] == REASON_JOIN_REFUSED
    assert (body["retry_block"], body["can_retry"]) == ("no_audio", False)
    assert "in 4 minutes" in body["retry_block_message"]
    refused.terminal_seen_at = datetime.now(UTC) - timedelta(minutes=6)
    session.commit()
    body = api.get(f"/api/meetings/{refused.id}").json()
    assert (body["retry_block"], body["retry_block_message"], body["can_retry"]) == (
        None,
        None,
        True,
    )


def test_retry_of_a_refused_meeting_sends_a_new_bot(api, session, template, fake_vexa):
    refused = add_meeting(
        session,
        template,
        status="failed",
        failure_reason=REASON_JOIN_REFUSED,
        completion_reason="google_meet_join_refused",
        terminal_seen_at=datetime.now(UTC) - timedelta(minutes=2),
        join_attempts=2,
    )
    early = api.post(f"/api/meetings/{refused.id}/retry")
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "not_retryable"
    assert "in 3 minutes" in early.json()["error"]["message"]
    refused.terminal_seen_at = datetime.now(UTC) - timedelta(minutes=6)
    session.commit()
    body = api.post(f"/api/meetings/{refused.id}/retry").json()
    assert body["status"] == "bot_sent" and body["failure_reason"] is None
    assert fake_vexa.sent == [MEET] and body["join_attempts"] == 3


@pytest.mark.parametrize(
    "path", ["/api/meetings/999", "/api/meetings/0", "/api/meetings/2147483648"]
)
def test_unknown_meeting_ids_are_404(api, path):
    error = error_of(api.get(path), 404, "not_found")
    assert error["message"] == "No such meeting"


# ---- meetings: writing ----


def create(api, template, url=MEET, **extra):
    return api.post("/api/meetings", {"url": url, "template_id": template.id, **extra})


def test_create_sends_a_bot_and_returns_the_meeting(api, template, fake_vexa, session):
    response = create(api, template)
    assert response.status_code == 201
    body = response.json()
    assert response.headers["location"] == f"/api/meetings/{body['id']}"
    assert body["status"] == "bot_sent" and body["title"] == "Google Meet abc-defg-hij"
    assert body["template_id"] == template.id and body["can_cancel"] is True
    assert fake_vexa.sent == [MEET]
    row = session.get(Meeting, body["id"])
    assert row.vexa_meeting_id == "4242" and row.started_polling_at is not None


def test_create_trims_the_link(api, template, fake_vexa):
    assert create(api, template, url=f"  {MEET}  ").status_code == 201
    assert fake_vexa.sent == [MEET]


@pytest.mark.parametrize(
    "url", ["", "https://example.com/x", "not a url", "https://meet.google.com/" + "a" * 2100]
)
def test_create_refuses_a_link_that_is_not_meet_or_zoom(api, template, fake_vexa, session, url):
    error = error_of(create(api, template, url=url), 400, "invalid_meeting_url")
    assert (
        error["message"] == "That is not a Google Meet or Zoom link. Paste the full meeting link."
    )
    assert fake_vexa.sent == [] and session.scalars(select(Meeting)).first() is None


def test_create_needs_a_real_template(api, template, fake_vexa):
    response = api.post("/api/meetings", {"url": MEET, "template_id": 9999})
    error_of(response, 400, "template_required")
    response = api.post("/api/meetings", {"url": MEET, "template_id": 0})
    error_of(response, 400, "template_required")
    response = api.post("/api/meetings", {"url": MEET, "template_id": 2**40})
    error_of(response, 400, "template_required")
    assert fake_vexa.sent == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"url": MEET},
        {"template_id": 1},
        {"url": MEET, "template_id": "1"},
        {"url": MEET, "template_id": True},
        {"url": 5, "template_id": 1},
        {"url": MEET, "template_id": 1, "extra": 1},
    ],
)
def test_create_validates_the_body(api, template, fake_vexa, body):
    error_of(api.post("/api/meetings", body), 422, "validation_error")
    assert fake_vexa.sent == []


def test_create_refuses_while_a_bot_is_active(api, template, session, fake_vexa):
    """The user's own running meeting fills the one slot: a plain busy (the old, longer
    `bot_already_active` wording is retired with the lock it belonged to)."""
    add_meeting(session, template, status="processing", step="audio")
    error = error_of(create(api, template), 409, "bot_busy")
    assert error["message"] == "The note taker is busy right now. Try again in a few minutes."
    assert fake_vexa.sent == []


def test_create_refuses_a_second_bot_in_the_same_meeting_with_room_to_spare(
    api, app, template, session, fake_vexa
):
    app.state.settings.bot_max_local = 3
    add_meeting(session, template, status="bot_sent")  # a bot is already in abc-defg-hij
    error = error_of(create(api, template), 409, "already_in_meeting")
    assert "already in that meeting" in error["message"]
    assert fake_vexa.sent == []


def test_create_refuses_a_third_bot_for_one_user(api, app, template, session, fake_vexa):
    app.state.settings.bot_max_local = 3
    add_meeting(session, template, status="bot_sent", native_id="aaa-aaaa-aaa")
    add_meeting(session, template, status="bot_sent", native_id="bbb-bbbb-bbb")
    error = error_of(create(api, template), 409, "user_limit")
    assert error["message"] == (
        "You have reached the most bots allowed at once (2). Wait for one to finish first."
    )
    assert fake_vexa.sent == []


def test_a_slot_that_is_only_locked_says_a_note_taker_is_being_set_up(
    api, template, session, fake_vexa, engine, monkeypatch
):
    """Not full, only locked by a send in flight: different words, same code, no user, no id."""
    from sqlalchemy import text

    from app import placement

    monkeypatch.setattr(placement, "LOCK_WAIT_SECONDS", 0.2)
    with engine.connect() as holder:
        holder.execute(
            text("select pg_advisory_xact_lock(cast(:a as int), cast(:b as int))"),
            {"a": placement.PLACEMENT_LOCK, "b": placement.LOCAL_HOST_ID},
        )
        error = error_of(create(api, template), 409, "bot_busy")
        holder.rollback()
    assert error["message"] == (
        "A note taker is being set up for another meeting. Try again in a minute."
    )
    assert fake_vexa.sent == []


def test_a_meeting_whose_bot_is_still_starting_cannot_be_cancelled(
    api, template, session, fake_vexa
):
    """Its send got no clear answer: a stop now would find nothing and the late bot would be
    orphaned. The page does not offer Cancel; the call says to try again in a minute."""
    from app import pipeline

    meeting = add_meeting(
        session, template, completion_reason=pipeline.SEND_UNCONFIRMED, vexa_meeting_id=None
    )
    meeting.bot_state_seen_at = datetime.now(UTC)
    session.commit()
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["can_cancel"] is False and body["status_text"] == "Starting the bot"
    error = error_of(api.post(f"/api/meetings/{meeting.id}/cancel"), 409, "bot_starting")
    assert "still starting" in error["message"]
    assert not hasattr(fake_vexa, "stopped")


def test_user_limit_zero_does_not_claim_the_user_has_bots(api, app, template, fake_vexa):
    app.state.settings.user_bot_limit = 0
    response = create(api, template)
    error = error_of(response, 409, "user_limit")
    assert "0 bots" not in error["message"] and "already have" not in error["message"]
    assert fake_vexa.sent == []


def test_user_limit_met_by_a_meeting_being_written_up_does_not_say_it_is_in_a_meeting(
    api, app, template, session, fake_vexa
):
    """`processing` meetings count toward the limit, but the bot has left them: the words must not
    call them bots in meetings."""
    app.state.settings.bot_max_local = 3
    add_meeting(session, template, status="processing", step="notes", native_id="aaa-aaaa-aaa")
    add_meeting(session, template, status="processing", step="audio", native_id="bbb-bbbb-bbb")
    error = error_of(create(api, template), 409, "user_limit")
    assert "in meetings" not in error["message"]
    assert "2" in error["message"]  # it still says what the limit is


def test_create_into_a_meeting_another_user_has_a_note_taker_in_is_the_neutral_refusal(
    api, app, template, session, fake_vexa, stranger
):
    app.state.settings.bot_max_local = 3
    theirs = Template(owner_user_id=stranger.id, name="Their template", instructions="x")
    session.add(theirs)
    session.commit()
    add_meeting(session, theirs, status="bot_sent", title="Board call")  # the same code as MEET
    response = create(api, template)
    error = error_of(response, 409, "meeting_has_note_taker")
    assert error["message"] == "A note taker is already in this meeting."
    for leak in (stranger.email, "Board", "Their template"):
        assert leak not in response.text
    assert fake_vexa.sent == []


def test_pasted_link_busy_answer_is_neutral_409_bot_busy_and_does_not_name_another_user(
    api, template, session, fake_vexa, stranger
):
    """Whoever holds the note taker, the answer is the same words: no name, no address, no id, no
    title, no hint that someone is recording (the stranger's meeting has a title to leak)."""
    theirs = Template(owner_user_id=stranger.id, name="Their template", instructions="x")
    session.add(theirs)
    session.commit()
    add_meeting(
        session, theirs, status="bot_sent", title="Board call: layoffs", native_id="zzz-zzzz-zzz"
    )
    response = create(api, template)
    error = error_of(response, 409, "bot_busy")
    assert error["message"] == "The note taker is busy right now. Try again in a few minutes."
    for leak in (stranger.email, "Board", "layoffs", "zzz-zzzz-zzz", "Their template", "already"):
        assert leak not in response.text
    assert str(stranger.id) not in error["message"]
    assert fake_vexa.sent == []


def test_a_bot_busy_with_someone_elses_meeting_is_a_neutral_busy(
    api, template, session, fake_vexa, stranger
):
    """A capacity refusal must not say whose meeting holds the bot (or that anyone is recording)."""
    theirs = Template(owner_user_id=stranger.id, name="General", instructions="x")
    session.add(theirs)
    session.commit()
    add_meeting(session, theirs, status="bot_sent", native_id="zzz-zzzz-zzz")
    error = error_of(create(api, template), 409, "bot_busy")
    assert error["message"] == "The note taker is busy right now. Try again in a few minutes."
    assert fake_vexa.sent == []


def test_someone_elses_meeting_does_not_block_when_there_is_room(
    api, app, template, session, fake_vexa, stranger
):
    app.state.settings.bot_max_local = 2
    theirs = Template(owner_user_id=stranger.id, name="General", instructions="x")
    session.add(theirs)
    session.commit()
    add_meeting(session, theirs, status="bot_sent", native_id="zzz-zzzz-zzz")
    assert create(api, template).status_code == 201


def test_create_reports_a_vexa_failure_without_its_detail(api, template, fake_vexa, session):
    fake_vexa.fail = True
    response = create(api, template, url=ZOOM)
    error = error_of(response, 502, "bot_send_failed")
    assert "secret" not in response.text and "SECRETPWD" not in response.text
    assert error["message"] == "The bot could not be sent. Please try again in a moment."
    assert session.scalars(select(Meeting)).first() is None


def test_cancel_removes_the_bot(api, template, session, fake_vexa):
    meeting = add_meeting(session, template)
    response = api.post(f"/api/meetings/{meeting.id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed" and body["failure_reason"] == REASON_CANCELLED
    assert body["can_cancel"] is False
    assert body["retry_block"] == "no_audio" and body["can_retry"] is False
    assert fake_vexa.stopped == [("google_meet", "abc-defg-hij")]


def test_cancel_only_while_waiting_for_the_bot(api, template, session, fake_vexa):
    meeting = add_meeting(session, template, status="processing", step="audio")
    error = error_of(api.post(f"/api/meetings/{meeting.id}/cancel"), 409, "not_waiting_for_bot")
    assert "still waiting for its bot" in error["message"]
    assert not hasattr(fake_vexa, "stopped")


def test_cancel_reports_a_vexa_failure_and_changes_nothing(api, template, session, fake_vexa):
    fake_vexa.stop_error = VexaError("secret detail")
    meeting = add_meeting(session, template)
    response = api.post(f"/api/meetings/{meeting.id}/cancel")
    error_of(response, 502, "bot_stop_failed")
    assert "secret" not in response.text
    session.refresh(meeting)
    assert meeting.status == "bot_sent"


def test_retry_resumes_a_failed_meeting_at_its_step(api, template, session):
    meeting = add_meeting(
        session, template, status="failed", step="transcribing", failure_reason="x"
    )
    response = api.post(f"/api/meetings/{meeting.id}/retry")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processing" and body["step"] == "transcribing"
    assert body["failure_reason"] is None and body["refresh_after_seconds"] == 30
    session.refresh(meeting)
    assert meeting.started_polling_at is not None


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "ready"},
        {"status": "processing"},
        {"status": "failed", "step": "waiting"},
        {"status": "failed", "step": "audio", "failure_reason": REASON_NO_SPEECH},
        {"status": "failed", "step": "audio", "audio_deleted_at": datetime.now(UTC)},
    ],
)
def test_retry_is_refused_when_it_cannot_help(api, template, session, fields):
    meeting = add_meeting(session, template, **fields)
    error = error_of(api.post(f"/api/meetings/{meeting.id}/retry"), 409, "not_retryable")
    assert error["message"] == "This meeting cannot be retried."
    session.refresh(meeting)
    assert meeting.status == fields["status"]


def test_regenerate_rewrites_the_notes_without_transcribing(api, template, session, fake_openai):
    other = Template(owner_user_id=template.owner_user_id, name="Short", instructions="Be brief.")
    session.add(other)
    session.commit()
    meeting = ready(session, template)
    fake_openai.notes = "\n\n".join(
        f"## {h}\nRewritten"
        for h in (
            "Summary",
            "Key points",
            "Decisions",
            "Action items",
            "Open questions",
            "Please check",
        )
    )
    response = api.post(f"/api/meetings/{meeting.id}/regenerate", {"template_id": other.id})
    assert response.status_code == 200
    body = response.json()
    assert body["template_id"] == other.id and "Rewritten" in body["notes"]
    assert "Rewritten" in body["notes_html"]
    assert len(fake_openai.chat_calls) == 1 and fake_openai.transcription_calls == 0
    session.refresh(meeting)
    assert meeting.template_id == other.id and "Rewritten" in meeting.notes


def test_regenerate_queues_the_new_notes_for_extraction(api, template, session, fake_openai):
    meeting = ready(session, template)
    meeting.extraction_status, meeting.extraction_attempts = "done", 1
    session.commit()
    fake_openai.notes = "\n\n".join(
        f"## {h}\nNone noted"
        for h in (
            "Summary",
            "Key points",
            "Decisions",
            "Action items",
            "Open questions",
            "Please check",
        )
    )
    response = api.post(f"/api/meetings/{meeting.id}/regenerate", {"template_id": template.id})
    assert response.status_code == 200
    session.refresh(meeting)
    assert (meeting.extraction_status, meeting.extraction_attempts) == ("pending", 0)


def test_regenerate_errors(api, template, session, fake_openai):
    waiting = add_meeting(session, template)
    error = error_of(
        api.post(f"/api/meetings/{waiting.id}/regenerate", {"template_id": template.id}),
        409,
        "not_ready",
    )
    assert "once a meeting is ready" in error["message"]
    finished = ready(session, template)
    error_of(
        api.post(f"/api/meetings/{finished.id}/regenerate", {"template_id": 9999}),
        400,
        "template_required",
    )
    error_of(api.post(f"/api/meetings/{finished.id}/regenerate", {}), 422, "validation_error")
    assert fake_openai.chat_calls == []


def test_regenerate_failure_keeps_the_old_notes(api, template, session, fake_openai):
    meeting = ready(session, template)
    fake_openai.notes = ""  # an empty reply makes generate_notes raise NotesFailed
    error = error_of(
        api.post(f"/api/meetings/{meeting.id}/regenerate", {"template_id": template.id}),
        502,
        "notes_failed",
    )
    assert error["message"] == "The notes could not be written. The old notes were kept."
    session.refresh(meeting)
    assert meeting.notes == NOTES


@pytest.mark.parametrize("action", ["cancel", "end", "retry", "regenerate"])
def test_meeting_actions_on_unknown_ids_are_404(api, action):
    body = {"template_id": 1} if action == "regenerate" else None
    error_of(api.post(f"/api/meetings/999/{action}", body), 404, "not_found")


# ---- templates ----


def test_templates_list_is_sorted_and_has_instructions(api, template, session):
    session.add(Template(owner_user_id=template.owner_user_id, name="Alpha", instructions="First."))
    session.commit()
    body = api.get("/api/templates").json()
    assert [t["name"] for t in body["templates"]] == ["Alpha", "General"]
    assert body["templates"][1] == {
        "id": template.id,
        "name": "General",
        "instructions": "Write general notes.",
    }


def test_create_template(api, session):
    response = api.post("/api/templates", {"name": "  Standup  ", "instructions": " Short. "})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Standup" and body["instructions"] == "Short."
    assert response.headers["location"] == f"/api/templates/{body['id']}"
    assert session.get(Template, body["id"]).name == "Standup"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"name": "", "instructions": "x"}, "Give the template a name and instructions."),
        ({"name": "x", "instructions": "   "}, "Give the template a name and instructions."),
        ({"name": "n" * 101, "instructions": "x"}, "The name can be at most 100 characters."),
        (
            {"name": "n", "instructions": "i" * 4001},
            "The instructions can be at most 4000 characters.",
        ),
    ],
)
def test_template_rules(api, session, body, message):
    error = error_of(api.post("/api/templates", body), 400, "invalid_template")
    assert error["message"] == message
    assert session.scalars(select(Template)).first() is None


def test_template_names_are_unique(api, template):
    error = error_of(
        api.post("/api/templates", {"name": "General", "instructions": "x"}), 400, "duplicate_name"
    )
    assert error["message"] == "A template with that name already exists."


def test_update_template(api, template, session):
    other = Template(owner_user_id=template.owner_user_id, name="Other", instructions="o")
    session.add(other)
    session.commit()
    response = api.put(
        f"/api/templates/{template.id}", {"name": "General", "instructions": "New text."}
    )
    assert response.status_code == 200  # keeping its own name is fine
    assert response.json() == {"id": template.id, "name": "General", "instructions": "New text."}
    response = api.put(f"/api/templates/{template.id}", {"name": "Renamed", "instructions": "x"})
    assert response.json()["name"] == "Renamed"
    error_of(
        api.put(f"/api/templates/{template.id}", {"name": "Other", "instructions": "x"}),
        400,
        "duplicate_name",
    )
    session.refresh(template)
    assert template.name == "Renamed"
    error_of(api.put("/api/templates/999", {"name": "a", "instructions": "b"}), 404, "not_found")
    error_of(api.put(f"/api/templates/{template.id}", {"name": "a"}), 422, "validation_error")


# ---- vocabulary ----


def test_vocabulary_add_list_and_delete(api, session):
    for term in ("Zed", "Acme", "  Beyric  "):
        response = api.post("/api/vocabulary", {"term": term})
        assert response.status_code == 201 and set(response.json()) == {"id", "term"}
    terms = api.get("/api/vocabulary").json()["terms"]
    assert [t["term"] for t in terms] == ["Acme", "Beyric", "Zed"]
    assert api.delete(f"/api/vocabulary/{terms[0]['id']}").status_code == 204
    assert api.delete(f"/api/vocabulary/{terms[0]['id']}").status_code == 404  # already gone
    assert [
        t.term for t in session.scalars(select(VocabularyTerm).order_by(VocabularyTerm.term))
    ] == ["Beyric", "Zed"]


@pytest.mark.parametrize(
    ("term", "message"),
    [
        ("", "Type a term to add."),
        ("   ", "Type a term to add."),
        ("a" * 101, "A term can be at most 100 characters."),
        ("a<b", "A term cannot contain new lines or the characters < and >."),
        ("a>b", "A term cannot contain new lines or the characters < and >."),
        ("a\nb", "A term cannot contain new lines or the characters < and >."),
    ],
)
def test_term_rules(api, term, message):
    assert (
        error_of(api.post("/api/vocabulary", {"term": term}), 400, "invalid_term")["message"]
        == message
    )


def test_duplicate_terms_are_refused(api):
    api.post("/api/vocabulary", {"term": "Beyric"})
    error = error_of(api.post("/api/vocabulary", {"term": " Beyric "}), 400, "duplicate_term")
    assert error["message"] == "That term is already in the list."


def test_deleting_an_unknown_term_is_404(api):
    error_of(api.delete("/api/vocabulary/999"), 404, "not_found")
    error_of(api.delete("/api/vocabulary/0"), 404, "not_found")


def test_term_body_is_validated(api):
    error_of(api.post("/api/vocabulary", {}), 422, "validation_error")
    error_of(api.post("/api/vocabulary", {"term": ["a"]}), 422, "validation_error")


LIVE_STATES = [
    ("joining", "Bot is joining the meeting"),
    (
        "awaiting_admission",
        "Waiting to be let in. Admit 'Beyric Note Taker (recording)' in the meeting",
    ),
    ("active", "Bot is in the meeting"),
    ("recording", "Recording"),
]


@pytest.mark.parametrize(("state", "words"), LIVE_STATES)
def test_detail_and_list_carry_the_bots_live_state_in_status_text(
    api, session, template, state, words
):
    meeting = add_meeting(
        session, template, completion_reason=state, bot_state_seen_at=datetime.now(UTC)
    )
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["status"] == "bot_sent" and body["step"] == "waiting"
    assert body["status_text"] == words
    assert body["refresh_after_seconds"] == 30  # the page keeps asking while the call runs
    assert set(body) == set(api.get(f"/api/meetings/{add_meeting(session, template).id}").json())
    listed = api.get("/api/meetings").json()["meetings"]
    assert [m["status_text"] for m in listed if m["id"] == meeting.id] == [words]


def test_the_live_state_does_not_leak_into_other_statuses(api, session, template):
    meeting = add_meeting(
        session, template, status="processing", step="notes", completion_reason="active"
    )
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["status_text"] == "Writing the notes"
    assert "completion_reason" not in body and "bot_state" not in body


def test_the_list_says_when_to_ask_again_only_while_a_meeting_is_being_worked_on(
    api, session, template
):
    ready(session, template)
    assert api.get("/api/meetings").json()["refresh_after_seconds"] is None
    waiting = add_meeting(session, template)
    assert api.get("/api/meetings").json()["refresh_after_seconds"] == 30
    waiting.status, waiting.step = "failed", "waiting"
    session.commit()
    assert api.get("/api/meetings").json()["refresh_after_seconds"] is None
    add_meeting(session, template, status="processing", step="audio")
    assert api.get("/api/meetings").json()["refresh_after_seconds"] == 30


def test_the_detail_says_when_the_worker_looks_stale_for_a_meeting_it_should_be_moving(
    api, session, template
):
    waiting = add_meeting(session, template)
    done = ready(session, template)
    session.add(AppState(id=1, worker_heartbeat_at=datetime.now(UTC) - timedelta(minutes=21)))
    session.commit()
    assert api.get(f"/api/meetings/{waiting.id}").json()["worker_stale"] is True
    assert api.get(f"/api/meetings/{done.id}").json()["worker_stale"] is False  # nothing to move
    session.get(AppState, 1).worker_heartbeat_at = datetime.now(UTC) - timedelta(minutes=19)
    session.commit()
    assert api.get(f"/api/meetings/{waiting.id}").json()["worker_stale"] is False


def test_a_live_state_the_worker_has_not_renewed_reads_checking_on_the_bot(api, session, template):
    old = datetime.now(UTC) - timedelta(minutes=5)
    meeting = add_meeting(session, template, completion_reason="active", bot_state_seen_at=old)
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["status_text"] == "Checking on the bot..."
    listed = api.get("/api/meetings").json()["meetings"]
    assert [m["status_text"] for m in listed] == ["Checking on the bot..."]
