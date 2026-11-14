# ruff: noqa: F811
"""The caller's own display name: GET and PUT /api/settings/name (`users.name`), "Your name" in
Settings. Cleaned like other user text, 1 to 100 characters, never empty, and the name the note
taker says in the meeting chat."""

import pytest
from sqlalchemy import select

from app import chat_notice, display_names, transcript_source
from app.display_names import NAME_LIMIT, DisplayNameInvalid, clean_display_name
from app.models import User
from tests.test_api import anon, api, error_of  # noqa: F401
from tests.test_bot_name import make
from tests.test_web import (  # noqa: F401
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_settings,
    make_user,
    owner,
    stranger,
)

ROUTE = "/api/settings/name"


def stored(session, user) -> str | None:
    session.expire_all()
    return session.scalar(select(User.name).where(User.id == user.id))


# ---- the cleaning ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ada Example", "Ada Example"),
        ("  Ada   \t Example \n", "Ada Example"),
        ("Zoë Müller", "Zoë Müller"),
        ("Ada \U0001f469‍\U0001f4bb", "Ada \U0001f469‍\U0001f4bb"),  # a joiner in an emoji
        ("Alex‮ Example", "Alex Example"),  # a direction override is removed, not kept
        ("Ada​Example", "AdaExample"),  # a zero-width space is removed
        ("Ada\x00 Example", "Ada Example"),  # a control character becomes a space
        ("N" * NAME_LIMIT, "N" * NAME_LIMIT),
    ],
)
def test_a_name_is_cleaned_like_other_user_text(raw, expected):
    assert clean_display_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", display_names.EMPTY),
        ("   \n\t ", display_names.EMPTY),
        ("⠀", display_names.EMPTY),  # Braille blank
        ("‮​", display_names.EMPTY),  # nothing left once cleaned
        ("N" * (NAME_LIMIT + 1), display_names.TOO_LONG),
    ],
)
def test_an_empty_invisible_or_long_name_is_refused(raw, message):
    with pytest.raises(DisplayNameInvalid) as caught:
        clean_display_name(raw)
    assert str(caught.value) == message


# ---- the route ----


def test_the_name_is_read_and_a_missing_one_is_null(api, session, owner):
    owner.name = None
    session.commit()
    assert api.get(ROUTE).json() == {"name": None}
    owner.name = "Ada Example"
    session.commit()
    assert api.get(ROUTE).json() == {"name": "Ada Example"}


def test_saving_cleans_stores_and_reads_back_and_the_session_shows_it(api, session, owner):
    response = api.put(ROUTE, {"name": "  Ada   Example‮ "})
    assert response.status_code == 200
    assert response.json() == {"name": "Ada Example"}
    assert stored(session, owner) == "Ada Example"
    assert api.get(ROUTE).json() == {"name": "Ada Example"}
    assert api.get("/api/session").json()["user"]["name"] == "Ada Example"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", display_names.EMPTY),
        ("   ", display_names.EMPTY),
        ("N" * 101, display_names.TOO_LONG),
    ],
)
def test_an_empty_or_long_name_is_422_with_a_plain_message_and_changes_nothing(
    api, session, owner, value, message
):
    api.put(ROUTE, {"name": "Kept Name"})
    response = api.put(ROUTE, {"name": value})
    assert error_of(response, 422, "name_invalid")["message"] == message
    assert stored(session, owner) == "Kept Name"


def test_an_account_with_no_name_still_cannot_save_an_empty_one(api, session, owner):
    owner.name = None
    session.commit()
    assert api.put(ROUTE, {"name": ""}).status_code == 422
    assert stored(session, owner) is None


@pytest.mark.parametrize(
    "body", [{"name": None}, {"name": 12}, {}, {"name": "A", "email": "x@y.z"}]
)
def test_the_body_is_strict(api, owner, body):
    assert api.put(ROUTE, body).status_code == 422


def test_it_touches_only_the_callers_own_row(api, session, owner, stranger):
    stranger.name = "Other Person"
    session.commit()
    api.put(ROUTE, {"name": "Mine"})
    assert stored(session, owner) == "Mine"
    assert stored(session, stranger) == "Other Person"


def test_it_needs_a_login_and_a_csrf_token(anon):
    assert anon.get(ROUTE).status_code == 401
    assert anon.put(ROUTE, {"name": "X"}).status_code in (401, 403)


def test_a_write_without_the_csrf_token_is_refused(api, owner):
    assert api.put(ROUTE, {"name": "X"}, csrf=False).status_code == 403


# ---- the chat notice says the name ----


def test_the_notice_says_the_host_until_a_name_is_set_then_the_name(api, session, owner):
    owner.name = None
    session.commit()
    settings = make_settings(bot_chat_notice_enabled=True, skribby_bot_name="Service Desk")
    provider, _ = make()
    before = transcript_source.send_options(session, provider, owner.id, settings)
    assert before["chat_notice"].startswith("Hi, I'm Service Desk. The host added me")
    api.put(ROUTE, {"name": "Ada Example"})
    after = transcript_source.send_options(session, provider, owner.id, settings)
    assert after["chat_notice"].startswith("Hi, I'm Service Desk. Ada Example added me")
    assert chat_notice.FALLBACK_OWNER == "the host"
