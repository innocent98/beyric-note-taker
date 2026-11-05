"""A person's time zone: GET and PUT /api/settings/time-zone (`user_settings.time_zone`), the
label a time carries, and the notes mail writing the meeting's start in that zone."""

from sqlalchemy import select

from app import timezones
from app.models import UserSettings
from tests.test_api import Api, anon, api, error_of  # noqa: F401  (fixtures used by name)
from tests.test_mail_upgrade import NOTES, NOW, deliver, notes_row, ready  # noqa: F401
from tests.test_web import (  # noqa: F401
    OTHER_EMAIL,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_user,
    owner,
    stranger,
)

URL = "/api/settings/time-zone"


def stored(session, user):
    session.expire_all()
    return session.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user.id))


# ---- the labels ----


def test_labels_are_utc_gmt_or_gmt_with_the_offset():
    from datetime import UTC, datetime

    moment = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    assert timezones.localise(moment, None)[1] == "UTC"
    assert timezones.localise(moment, "UTC")[1] == "UTC"
    assert timezones.localise(moment, "Africa/Lagos")[1] == "GMT+1"
    assert timezones.localise(moment, "Europe/London")[1] == "GMT"
    assert timezones.localise(moment, "America/New_York")[1] == "GMT-5"
    assert timezones.localise(moment, "Asia/Kolkata")[1] == "GMT+5:30"
    assert timezones.localise(moment, "Asia/Kolkata")[0].hour == 17
    # An unknown name falls back to UTC rather than failing a mail.
    assert timezones.localise(moment, "Mars/Olympus")[1] == "UTC"


# ---- the API ----


def test_the_default_is_utc_and_not_set(api, session, owner):
    assert stored(session, owner) is None
    assert api.get(URL).json() == {"time_zone": "UTC", "is_set": False}


def test_setting_a_zone_is_stored_and_read_back(api, session, owner):
    assert api.put(URL, {"time_zone": "Africa/Lagos"}).json() == {
        "time_zone": "Africa/Lagos",
        "is_set": True,
    }
    assert stored(session, owner) == "Africa/Lagos"
    assert api.get(URL).json() == {"time_zone": "Africa/Lagos", "is_set": True}
    api.put(URL, {"time_zone": "UTC"})
    assert api.get(URL).json() == {"time_zone": "UTC", "is_set": True}


def test_an_unknown_zone_is_refused_and_nothing_is_stored(api, session, owner):
    for bad in ("Mars/Olympus", "", "../etc/passwd", "x" * 80):
        error_of(api.put(URL, {"time_zone": bad}), 422, "time_zone_unsupported")
    assert stored(session, owner) is None


def test_it_needs_a_login(anon, owner, session):
    error_of(anon.get(URL), 401, "not_authenticated")
    error_of(anon.put(URL, {"time_zone": "Asia/Tokyo"}), 401, "not_authenticated")
    assert stored(session, owner) is None


# ---- the mail ----


def test_the_notes_mail_writes_the_start_in_the_owners_zone(session, ready):
    owner_row = session.get(UserSettings, ready.owner_user_id)
    assert owner_row is None
    session.add(UserSettings(user_id=ready.owner_user_id, time_zone="Africa/Lagos"))
    session.commit()
    notes_row(session, ready)
    sent, mailer = deliver(session)
    assert sent == 1
    [(message, _)] = mailer.sent
    # The meeting started 30 minutes before NOW (12:00 UTC): 11:30 UTC is 12:30 in Lagos.
    assert "Zoom 88205679674 · 03 Nov 2026, 12:30 GMT+1" in message.text
    assert "UTC" not in message.text.split("Your meeting notes are ready")[0]


def test_the_notes_mail_stays_utc_without_a_zone(session, ready):
    notes_row(session, ready)
    sent, mailer = deliver(session)
    [(message, _)] = mailer.sent
    assert "Zoom 88205679674 · 03 Nov 2026, 11:30 UTC" in message.text


def test_the_session_carries_the_zone_for_the_pages(api, owner):
    assert api.get("/api/session").json()["user"]["time_zone"] is None
    api.put(URL, {"time_zone": "Africa/Lagos"})
    assert api.get("/api/session").json()["user"]["time_zone"] == "Africa/Lagos"
