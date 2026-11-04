"""`DELETE /api/templates/{id}`: what happens to everything that points at a template.

A template is retired, not erased (`templates.deleted_at`): a meeting keeps the template id it was
written with, so its notes and the name it shows survive, and a meeting that is still being written
(or notes being rewritten) finishes with the template it started with. What is gone is every way to
*choose* it: the list, the pickers, the calendar setting, a new meeting, a new rewrite.
"""

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import accounts, pipeline, services
from app.models import Meeting, ScheduledJoin, Template, User, UserSettings
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    api,
    error_of,
    ready,
)
from tests.test_web import (  # noqa: F401  (fixtures used by name)
    MEET,
    NOTES,
    OTHER_EMAIL,
    add_meeting,
    anon_client,
    app,
    client,
    fake_openai,
    fake_vexa,
    owner,
    stranger,
    template,
)


def make_template(session, user, name, instructions="Write notes.") -> Template:
    row = Template(owner_user_id=user.id, name=name, instructions=instructions)
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def second(session, owner, template):
    """A second template, so that `template` is not the owner's last one."""
    return make_template(session, owner, "Interview", "Ask about the candidate.")


def live_names(api) -> list[str]:
    return [t["name"] for t in api.get("/api/templates").json()["templates"]]


# ---- the route ----


def test_delete_removes_the_template_from_the_list_and_answers_204(api, template, second):
    response = api.delete(f"/api/templates/{second.id}")
    assert response.status_code == 204
    assert response.content == b""
    assert live_names(api) == ["General"]


def test_delete_needs_a_login(anon, template, second):
    error_of(anon.delete(f"/api/templates/{second.id}"), 401, "not_authenticated")


def test_delete_needs_the_csrf_token(api, session, template, second):
    error_of(api.delete(f"/api/templates/{second.id}", csrf=False), 403, "csrf_rejected")
    error_of(api.delete(f"/api/templates/{second.id}", csrf="wrong"), 403, "csrf_rejected")
    session.expire_all()
    assert session.get(Template, second.id).deleted_at is None


def test_someone_elses_template_is_404_and_stays(api, session, owner, stranger, template):
    theirs = make_template(session, stranger, "Theirs")
    make_template(session, stranger, "Theirs too")
    error_of(api.delete(f"/api/templates/{theirs.id}"), 404, "not_found")
    session.expire_all()
    assert session.get(Template, theirs.id).deleted_at is None


@pytest.mark.parametrize("template_id", [0, -1, 999_999, 2**62, 2**70])
def test_a_template_that_does_not_exist_is_404(api, template, second, template_id):
    error_of(api.delete(f"/api/templates/{template_id}"), 404, "not_found")


def test_a_second_delete_of_the_same_template_is_404_never_500(api, template, second):
    assert api.delete(f"/api/templates/{second.id}").status_code == 204
    error_of(api.delete(f"/api/templates/{second.id}"), 404, "not_found")
    assert live_names(api) == ["General"]


def test_a_deleted_template_cannot_be_edited(api, template, second):
    api.delete(f"/api/templates/{second.id}")
    error_of(
        api.put(f"/api/templates/{second.id}", {"name": "Back", "instructions": "x"}),
        404,
        "not_found",
    )


def test_the_name_of_a_deleted_template_can_be_used_again(api, session, owner, template, second):
    api.delete(f"/api/templates/{second.id}")
    response = api.post("/api/templates", {"name": "Interview", "instructions": "A new one."})
    assert response.status_code == 201
    assert response.json()["id"] != second.id
    # ... but a live name is still taken, and a rename may take a deleted name
    error_of(
        api.post("/api/templates", {"name": "Interview", "instructions": "x"}),
        400,
        "duplicate_name",
    )
    api.delete(f"/api/templates/{response.json()['id']}")
    other = api.post("/api/templates", {"name": "Other", "instructions": "x"}).json()
    renamed = api.put(f"/api/templates/{other['id']}", {"name": "Interview", "instructions": "x"})
    assert renamed.status_code == 200


# ---- the last template ----


def test_the_last_template_is_refused_with_a_plain_message(api, session, template):
    error = error_of(api.delete(f"/api/templates/{template.id}"), 409, "last_template")
    assert (
        error["message"] == "Keep at least one template. Add another one first, then delete this."
    )
    assert live_names(api) == ["General"]


def test_deleted_templates_do_not_count_as_remaining(api, template, second):
    api.delete(f"/api/templates/{second.id}")
    error_of(api.delete(f"/api/templates/{template.id}"), 409, "last_template")


def test_the_last_one_can_go_once_another_exists(api, template):
    error_of(api.delete(f"/api/templates/{template.id}"), 409, "last_template")
    added = api.post("/api/templates", {"name": "Standup", "instructions": "Short."}).json()
    assert api.delete(f"/api/templates/{template.id}").status_code == 204
    assert live_names(api) == ["Standup"]
    error_of(api.delete(f"/api/templates/{added['id']}"), 409, "last_template")


def test_the_last_template_rule_is_per_user(api, session, stranger, template):
    make_template(session, stranger, "Theirs")
    make_template(session, stranger, "Theirs too")
    error_of(api.delete(f"/api/templates/{template.id}"), 409, "last_template")


# ---- the built-in templates ----


def test_the_four_seeded_templates_are_ordinary_per_user_rows(api, session, owner, stranger):
    accounts.create_user_defaults(session, owner)
    accounts.create_user_defaults(session, stranger)
    general = session.scalars(
        select(Template).where(Template.owner_user_id == owner.id, Template.name == "General")
    ).one()
    assert api.delete(f"/api/templates/{general.id}").status_code == 204
    assert live_names(api) == ["Client call", "Interview", "Team update"]
    # the other person's own "General" is a different row and is still there
    theirs = session.scalars(
        select(Template).where(Template.owner_user_id == stranger.id, Template.name == "General")
    ).one()
    session.expire_all()
    assert theirs.deleted_at is None


def test_all_but_one_of_the_seeded_templates_can_be_deleted(api, session, owner):
    rows = accounts.create_user_defaults(session, owner)
    for row in rows[:-1]:
        assert api.delete(f"/api/templates/{row.id}").status_code == 204
    error_of(api.delete(f"/api/templates/{rows[-1].id}"), 409, "last_template")
    assert live_names(api) == ["Interview"]


# ---- meetings written with the template ----


def test_a_meeting_written_with_it_keeps_its_notes_and_the_name(api, session, template, second):
    meeting = ready(session, second)
    api.delete(f"/api/templates/{second.id}")
    body = api.get(f"/api/meetings/{meeting.id}").json()
    assert body["notes"].strip() == NOTES
    assert body["template_id"] == second.id
    assert body["template_name"] == "Interview"
    listed = api.get("/api/meetings").json()["meetings"]
    assert [(m["id"], m["template_name"]) for m in listed] == [(meeting.id, "Interview")]


def test_a_meeting_names_its_live_template_as_it_is_now(api, session, template):
    meeting = ready(session, template)
    assert api.get(f"/api/meetings/{meeting.id}").json()["template_name"] == "General"
    api.put(f"/api/templates/{template.id}", {"name": "Renamed", "instructions": "x"})
    assert api.get(f"/api/meetings/{meeting.id}").json()["template_name"] == "Renamed"


def test_a_meeting_written_with_it_can_still_be_rewritten_with_another(
    api, session, template, second, fake_openai
):
    meeting = ready(session, second)
    api.delete(f"/api/templates/{second.id}")
    response = api.post(f"/api/meetings/{meeting.id}/regenerate", {"template_id": template.id})
    assert response.status_code == 200
    assert response.json()["template_id"] == template.id
    assert response.json()["template_name"] == "General"


def test_a_deleted_template_cannot_be_chosen_for_a_new_meeting_or_a_rewrite(
    api, session, template, second, fake_openai
):
    finished = ready(session, template)
    api.delete(f"/api/templates/{second.id}")
    error_of(
        api.post("/api/meetings", {"url": MEET, "template_id": second.id}),
        400,
        "template_required",
    )
    error_of(
        api.post(f"/api/meetings/{finished.id}/regenerate", {"template_id": second.id}),
        400,
        "template_required",
    )
    assert fake_openai.chat_calls == []
    assert session.scalars(select(Meeting)).all() == [finished]


def test_a_meeting_still_being_written_finishes_with_the_deleted_template(
    api, session, template, second, fake_openai
):
    meeting = add_meeting(
        session,
        second,
        status="processing",
        step="notes",
        transcript="We agreed to ship on Friday.",
        speaker_transcript="We agreed to ship on Friday.",
    )
    api.delete(f"/api/templates/{second.id}")
    assert pipeline._step_notes(session, meeting, fake_openai) is True
    session.refresh(meeting)
    assert (meeting.status, meeting.step) == ("ready", "done")
    assert meeting.notes.strip() == NOTES
    assert "Ask about the candidate." in str(fake_openai.chat_calls[0])
    assert meeting.template_id == second.id
    assert api.get(f"/api/meetings/{meeting.id}").json()["template_name"] == "Interview"


def test_a_rewrite_in_flight_finishes_when_its_template_is_deleted_meanwhile(
    api, session, engine, owner, template, second, fake_openai
):
    meeting = ready(session, template)
    real_chat = fake_openai._chat

    def delete_while_the_model_works(**kwargs):
        with Session(engine) as other:  # another request, while this one waits for the model
            services.delete_template(other, other.get(User, owner.id), second.id)
        return real_chat(**kwargs)

    fake_openai.chat.completions.create = delete_while_the_model_works
    response = api.post(f"/api/meetings/{meeting.id}/regenerate", {"template_id": second.id})
    assert response.status_code == 200, response.text  # no 500
    body = response.json()
    assert body["template_id"] == second.id and body["template_name"] == "Interview"
    assert body["notes"].strip() == NOTES
    assert live_names(api) == ["General"]
    # pressing it again is refused plainly, as for any template that is gone
    error_of(
        api.post(f"/api/meetings/{meeting.id}/regenerate", {"template_id": second.id}),
        400,
        "template_required",
    )


# ---- the calendar template ----


def settings_of(session, user) -> UserSettings:
    session.expire_all()
    return session.get(UserSettings, user.id)


def test_the_calendar_setting_falls_back_to_the_oldest_remaining_template(
    api, session, owner, template, second
):
    third = make_template(session, owner, "Team update")
    session.add(UserSettings(user_id=owner.id, calendar_template_id=second.id))
    session.commit()
    assert api.delete(f"/api/templates/{second.id}").status_code == 204
    # "General" is the oldest left; never the deleted one, never a dangling id
    assert settings_of(session, owner).calendar_template_id == template.id
    assert third.id != settings_of(session, owner).calendar_template_id


def test_the_fallback_skips_the_template_being_deleted_when_it_is_the_oldest(
    api, session, owner, template, second
):
    session.add(UserSettings(user_id=owner.id, calendar_template_id=template.id))
    session.commit()
    assert api.delete(f"/api/templates/{template.id}").status_code == 204
    assert settings_of(session, owner).calendar_template_id == second.id


def test_a_calendar_setting_pointing_elsewhere_is_left_alone(api, session, owner, template, second):
    session.add(UserSettings(user_id=owner.id, calendar_template_id=template.id))
    session.commit()
    api.delete(f"/api/templates/{second.id}")
    assert settings_of(session, owner).calendar_template_id == template.id


def test_the_default_calendar_setting_stays_the_default(api, session, owner, template, second):
    session.add(UserSettings(user_id=owner.id, calendar_template_id=None))
    session.commit()
    api.delete(f"/api/templates/{template.id}")
    assert settings_of(session, owner).calendar_template_id is None


def test_another_users_calendar_setting_is_not_touched(
    api, session, owner, stranger, template, second
):
    theirs = make_template(session, stranger, "Interview")
    session.add_all(
        [
            UserSettings(user_id=owner.id, calendar_template_id=second.id),
            UserSettings(user_id=stranger.id, calendar_template_id=theirs.id),
        ]
    )
    session.commit()
    api.delete(f"/api/templates/{second.id}")
    assert settings_of(session, stranger).calendar_template_id == theirs.id


def add_join(session, user, state, template_id):
    start = datetime.now(UTC) + timedelta(hours=3)
    join = ScheduledJoin(
        user_id=user.id,
        source="calendar",
        platform="google_meet",
        native_id=f"abc-{state}",
        meeting_url="https://meet.google.com/abc-defg-hij",
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        run_at=start - timedelta(minutes=1),
        deadline_at=start + timedelta(minutes=15),
        state=state,
        template_id=template_id,
    )
    session.add(join)
    session.commit()
    return join


def test_planned_calendar_joins_follow_the_fallback_and_sent_ones_keep_theirs(
    api, session, owner, template, second
):
    session.add(UserSettings(user_id=owner.id, calendar_template_id=second.id))
    session.commit()
    planned = add_join(session, owner, "scheduled", second.id)
    waiting = add_join(session, owner, "no_capacity", second.id)
    sent = add_join(session, owner, "joined", second.id)
    api.delete(f"/api/templates/{second.id}")
    session.expire_all()
    assert session.get(ScheduledJoin, planned.id).template_id == template.id
    assert session.get(ScheduledJoin, waiting.id).template_id == template.id
    assert session.get(ScheduledJoin, sent.id).template_id == second.id  # its bot is in


def test_a_deleted_template_cannot_be_chosen_for_calendar_meetings(api, session, template, second):
    # `services.template_or_404` is what PUT /api/calendar/settings uses to check the choice
    api.delete(f"/api/templates/{second.id}")
    with Session(session.get_bind()) as other:
        with pytest.raises(Exception) as caught:
            services.template_or_404(other, other.get(User, template.owner_user_id), second.id)
    assert getattr(caught.value, "status_code", None) == 404


# ---- two requests at once ----


def _delete_in_thread(engine, user_id, template_id, outcome):
    with Session(engine) as session:
        try:
            services.delete_template(session, session.get(User, user_id), template_id)
            outcome.append("deleted")
        except services.Refused as exc:
            outcome.append(exc.code)
        except Exception as exc:  # noqa: BLE001  (HTTPException 404 and anything unexpected)
            outcome.append(getattr(exc, "status_code", repr(exc)))


def _hold_the_users_templates(engine, user_id):
    """What a delete does first: lock the user's live templates. Returns the open session."""
    holder = Session(engine)
    holder.scalars(
        select(Template.id)
        .where(Template.owner_user_id == user_id, Template.deleted_at.is_(None))
        .order_by(Template.id)
        .with_for_update()
    ).all()
    return holder


def test_two_deletes_of_the_last_two_templates_leave_one(session, engine, owner, template, second):
    """Both would see two templates; the second must look again after the first one committed."""
    holder = _hold_the_users_templates(engine, owner.id)
    outcome: list = []
    waiter = threading.Thread(target=_delete_in_thread, args=(engine, owner.id, second.id, outcome))
    waiter.start()
    time.sleep(0.5)
    assert waiter.is_alive(), "the second delete should wait for the first"
    holder.execute(
        Template.__table__.update()
        .where(Template.id == template.id)
        .values(deleted_at=datetime.now(UTC))
    )
    holder.commit()
    holder.close()
    waiter.join(10)
    assert outcome == ["last_template"]
    session.expire_all()
    assert session.get(Template, second.id).deleted_at is None


def test_two_deletes_of_the_same_template_at_once_are_one_204_and_one_404(
    session, engine, owner, template, second
):
    holder = _hold_the_users_templates(engine, owner.id)
    outcome: list = []
    waiter = threading.Thread(target=_delete_in_thread, args=(engine, owner.id, second.id, outcome))
    waiter.start()
    time.sleep(0.5)
    assert waiter.is_alive()
    holder.execute(
        Template.__table__.update()
        .where(Template.id == second.id)
        .values(deleted_at=datetime.now(UTC))
    )
    holder.commit()
    holder.close()
    waiter.join(10)
    assert outcome == [404]


# ---- the enumeration test's blind spot: the owner can still delete ----


def test_the_owners_own_delete_works_after_a_strangers_refused_one(
    api, anon, session, owner, stranger, template, second
):
    theirs = make_template(session, stranger, "Theirs")
    make_template(session, stranger, "Theirs too")
    anon.log_in(email=OTHER_EMAIL)
    error_of(anon.delete(f"/api/templates/{second.id}"), 404, "not_found")
    anon.log_in()
    assert anon.delete(f"/api/templates/{second.id}").status_code == 204
    session.expire_all()
    assert session.get(Template, theirs.id).deleted_at is None
