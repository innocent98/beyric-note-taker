"""The words and the one new field of the calendar screens for Outlook (Outlook phase O, M6, server
side): `microsoft_enabled` on `GET /api/calendar`, the provider-aware sentences of
`app/calendar_text.py`, the disconnect notification and its mail footer, and the Teams "Cannot
join" row while Teams joining is off. Google's words are pinned unchanged."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import alerts, calendar_states, calendar_text, mail
from app.calendar_source import AuthRevoked
from app.models import CalendarConnection, CalendarEvent, MailOutbox, Notification
from tests import test_microsoft_connect as mc
from tests.test_calendar_connect import browser
from tests.test_web import make_settings

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
capp, google, ms, settings, vault_key, web = (
    mc.capp,
    mc.google,
    mc.ms,
    mc.settings,
    mc.vault_key,
    mc.web,
)
owner, fake_openai, fake_vexa = mc.owner, mc.fake_openai, mc.fake_vexa

GOOGLE_RECONNECT = {
    "revoked": "Google access was removed. Connect again to carry on.",
    "expired": "Google access ran out. Connect again to carry on.",
    "error": "Google Calendar stopped working. Connect again to carry on.",
}
MICROSOFT_RECONNECT = {
    "revoked": "Microsoft access was removed. Connect again to carry on.",
    "expired": "Microsoft access ran out. Connect again to carry on.",
    "error": "Microsoft Outlook stopped working. Connect again to carry on.",
}


# ---- the sentences ----


@pytest.mark.parametrize("reason", sorted(GOOGLE_RECONNECT))
def test_each_provider_gets_its_own_reconnect_sentence(reason):
    assert calendar_text.reconnect_text("google", reason) == GOOGLE_RECONNECT[reason]
    assert calendar_text.reconnect_text("microsoft", reason) == MICROSOFT_RECONNECT[reason]


def test_an_unknown_reason_or_provider_falls_back_to_the_generic_line():
    assert calendar_text.reconnect_text("google", "weird") == "Google needs you to connect again."
    assert (
        calendar_text.reconnect_text("microsoft", None) == "Microsoft needs you to connect again."
    )
    assert calendar_text.reconnect_text(None, "revoked") == GOOGLE_RECONNECT["revoked"]
    assert calendar_text.reconnect_text("ics", "revoked") == GOOGLE_RECONNECT["revoked"]


def test_google_words_are_unchanged():
    assert calendar_text.RECONNECT_TEXT == GOOGLE_RECONNECT
    assert calendar_text.GENERIC_RECONNECT_TEXT == "Google needs you to connect again."
    assert calendar_text.error_text("AuthRevoked: x") == (
        "Google no longer lets Beyric read your calendar. Connect again to fix this."
    )
    assert calendar_text.error_text("AuthRevoked: x", "google") == calendar_text.REVOKED_ERROR_TEXT


def test_the_last_error_of_a_microsoft_connection_names_microsoft():
    assert calendar_text.error_text("AuthRevoked: Microsoft no longer honours it", "microsoft") == (
        "Microsoft no longer lets Beyric read your calendar. Connect again to fix this."
    )
    assert calendar_text.error_text(
        "TransientError: Microsoft Graph answered HTTP 503", "microsoft"
    ) == (calendar_text.GENERIC_ERROR_TEXT)
    assert calendar_text.error_text(None, "microsoft") is None
    # nothing of the recorded text reaches a person
    assert "503" not in calendar_text.error_text("TransientError: HTTP 503", "microsoft")


def test_state_text_follows_the_connections_provider():
    conn = CalendarConnection(
        provider="microsoft", status="needs_reconnect", needs_reconnect_reason="revoked"
    )
    assert calendar_text.state_text(conn, "needs_reconnect") == MICROSOFT_RECONNECT["revoked"]
    conn.provider = "google"
    assert calendar_text.state_text(conn, "needs_reconnect") == GOOGLE_RECONNECT["revoked"]
    assert calendar_text.state_text(conn, "connected") == "Connected"


# ---- GET /api/calendar ----


def test_microsoft_enabled_is_false_until_the_flag_is_on(
    engine, settings, ms, google, fake_vexa, fake_openai, owner
):
    off = settings.model_copy(update={"microsoft_calendar_enabled": False})
    web_off = browser(mc.build_app(engine, off, ms, google, fake_vexa, fake_openai))
    assert web_off.get("/api/calendar").json()["microsoft_enabled"] is False


def test_microsoft_enabled_is_true_when_the_server_has_it_on(web):
    assert web.get("/api/calendar").json()["microsoft_enabled"] is True


def test_a_server_with_only_microsoft_still_answers(
    engine, settings, ms, google, fake_vexa, fake_openai, owner
):
    """Google not configured, Outlook on: the Settings page can still read its state."""
    only = settings.model_copy(update={"google_client_id": "", "google_client_secret": ""})
    app = mc.build_app(engine, only, ms, google, fake_vexa, fake_openai)
    from app import google_oauth

    app.dependency_overrides[google_oauth.get_google] = lambda: None
    body = browser(app).get("/api/calendar")
    assert body.status_code == 200 and body.json()["microsoft_enabled"] is True


def test_a_server_with_neither_provider_answers_404(
    engine, settings, ms, google, fake_vexa, fake_openai, owner
):
    from app import google_oauth

    neither = settings.model_copy(update={"microsoft_calendar_enabled": False})
    app = mc.build_app(engine, neither, ms, google, fake_vexa, fake_openai)
    app.dependency_overrides[google_oauth.get_google] = lambda: None
    mc.error_of(browser(app).get("/api/calendar"), 404, "calendar_unavailable")


def add_conn(session, user, **fields):
    values = {
        "user_id": user.id,
        "provider": "microsoft",
        "status": "connected",
        "granted_scopes": "Calendars.Read User.Read",
        "account_email": "ada@outlook.example.test",
        "account_subject": "oid-1",
        "tenant_id": "9188040d-6c67-4c5b-b112-36a304b66dad",
        "account_kind": "personal",
        "connected_at": NOW - timedelta(days=1),
    }
    conn = CalendarConnection(**{**values, **fields})
    session.add(conn)
    session.commit()
    return conn


def test_a_connected_outlook_calendar_reads_as_the_designer_draws_it(web, session, owner):
    add_conn(session, owner, last_sync_at=NOW)
    body = web.get("/api/calendar").json()
    assert body["provider"] == "microsoft" and body["account_email"] == "ada@outlook.example.test"
    assert body["connected"] is True and body["state"] == "connected"
    assert body["state_text"] == "Connected" and body["last_error"] is None


def test_a_dead_outlook_connection_says_microsoft_in_the_state_and_the_error(web, session, owner):
    add_conn(
        session,
        owner,
        status="needs_reconnect",
        needs_reconnect_reason="revoked",
        last_error="AuthRevoked: Microsoft no longer honours the refresh token (HTTP 400)",
    )
    body = web.get("/api/calendar").json()
    assert body["state"] == "needs_reconnect" and body["connected"] is False
    assert body["state_text"] == MICROSOFT_RECONNECT["revoked"]
    assert body["last_error"] == (
        "Microsoft no longer lets Beyric read your calendar. Connect again to fix this."
    )
    assert "invalid_grant" not in str(body) and "HTTP" not in str(body)


def test_a_teams_event_is_cannot_join_while_teams_joining_is_off(web, session, owner):
    conn = add_conn(session, owner, last_sync_at=NOW)
    start = datetime.now(UTC) + timedelta(hours=2)
    session.add(
        CalendarEvent(
            user_id=owner.id,
            connection_id=conn.id,
            source_event_id="teams-1",
            title="Planning",
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            platform="teams",
            meeting_url="https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%40thread.v2/0",
            native_id=None,
            organizer_self=True,
            self_response="accepted",
            attendee_count=2,
            status="confirmed",
        )
    )
    session.commit()
    [row] = web.get("/api/calendar").json()["upcoming"]
    assert row["will_join"] is False and row["will_join_text"] == "Cannot join"
    assert row["reason_text"] == "Microsoft Teams meetings cannot be joined yet"
    assert row["platform"] == "teams" and row["platform_label"] == "Microsoft Teams"
    assert "meeting_url" not in row and "meetup-join" not in str(
        row
    )  # a Teams link stays server side
    error = web.put(f"/api/calendar/events/{row['id']}/override", {"override": True})
    mc.error_of(error, 409, "not_joinable")


# ---- the disconnect notification and its mail ----


@pytest.fixture
def mail_on(monkeypatch):
    monkeypatch.setattr(alerts, "get_settings", lambda: make_settings())


@pytest.mark.parametrize(
    "provider,expected", [("microsoft", MICROSOFT_RECONNECT), ("google", GOOGLE_RECONNECT)]
)
def test_the_bell_and_the_mail_use_the_connections_own_words(
    session, owner, mail_on, provider, expected
):
    conn = add_conn(session, owner)
    conn.provider = provider
    session.commit()
    calendar_states.mark_needs_reconnect(session, conn, "revoked", NOW)
    session.commit()
    (note,) = session.scalars(
        select(Notification).where(Notification.kind == "calendar.disconnected")
    )
    assert note.title == note.body == expected["revoked"]
    (row,) = session.scalars(select(MailOutbox))
    assert row.template == "calendar_disconnected"
    message = mail.render(row.template, row.payload, "ada@example.test", make_settings())
    wanted = "Microsoft Outlook" if provider == "microsoft" else "Google Calendar"
    assert f"your {wanted} is connected to Beyric Note Taker" in message.text


def test_the_disconnect_mail_footer_without_a_provider_is_googles_as_before():
    message = mail.render("calendar_disconnected", {}, "ada@example.test", make_settings())
    assert "your Google Calendar is connected" in message.text


def test_an_idle_expired_microsoft_grant_is_shown_as_expired(session, owner):
    conn = add_conn(session, owner)
    reason = calendar_states.reason_for(conn, AuthRevoked("x", expired=True), NOW, testing=False)
    assert reason == "expired"
    assert calendar_text.reconnect_text("microsoft", reason) == MICROSOFT_RECONNECT["expired"]
