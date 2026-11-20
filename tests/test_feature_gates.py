"""The feature gates (billing stage S3, spec 2.2 and 2.3): Ask Beyric, a NEW translation and
rewriting notes need a person in a state that may use them, in three modes (`off`, `shadow`,
`enforce`). Real Postgres: the state of a person is read from their grants.

The table test puts a person in every state of spec 2.2 and asks every gate in every mode. The
call-site tests prove the gate sits in the function that calls the model, so no route, page or
share can go round it, and that everything the spec leaves open stays open."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select, text

from app import entitlements, feature_gates, services, teams
from app.models import (
    AiRun,
    HourGrant,
    MeetingShare,
    MeetingTranslation,
    TeamMember,
    Template,
    UsageAllocation,
    UsageEntry,
)
from app.services import Refused
from app.usage import email_hash
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    client,
    error_of,
    fake_bot,
    fake_openai,
    make_user,
    owner,
    ready,
    stranger,
    template,
)
from tests.test_api_translation import ask as ask_translation
from tests.test_api_translation import translatable, work
from tests.test_api_translation import url as translation_url
from tests.test_api_workspace import extracted
from tests.test_ask import meeting as ask_meeting
from tests.test_ask import model  # noqa: F401  (fixture used by name)
from tests.test_ask import post as post_ask
from tests.test_team_visibility import (  # noqa: F401
    TEAMMATE_EMAIL,
    sign_in,
    teammate,
)
from tests.test_team_visibility import share as share_with_team
from tests.test_translation import FakeModel
from tests.test_web import add_meeting, make_settings, token_from

LOGGER = "app.feature_gates"
T0 = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
HOUR = 3600
GATES = entitlements.FEATURES

# Every state of spec 2.2, and whether the three gates are open in it. The first nine are built
# from real grants; `past_due` and `lapsed` need subscriptions (stage S8), so they are put in by
# replacing `entitlements.status` (see `in_state`).
OPEN = True
CLOSED = False
STATES = {
    "trial": OPEN,  # a trial with hours left
    "pilot": OPEN,  # a pilot grant with hours left
    "active": OPEN,  # a paid plan period with hours
    "active_hours_spent": OPEN,  # hours used up: Ask, translate and rewrite use none
    "admin": OPEN,  # no limit
    "past_due": OPEN,  # (S8) the 3 days after a failed renewal keep them
    "none": CLOSED,  # never subscribed: the teammate who only reads
    "trial_used_up": CLOSED,  # the 3 hours used
    "trial_ended": CLOSED,  # the 14 days passed
    "pilot_used_up": CLOSED,  # a pilot grant with no hours left is "none" (spec 2.2, last row)
    "pack_only": CLOSED,  # a pack is hours to record with, not a plan
    "admin_gift_only": CLOSED,  # a gift is hours too, not a plan
    "lapsed": CLOSED,  # (S8) an expired plan
}


def settings_for(mode: str):
    return make_settings(feature_gates_mode=mode)


def add_grant(session, user, kind, seconds, *, starts=None, expires=None, now=T0):
    row = HourGrant(
        user_id=user.id,
        email_hash=email_hash(user.email),
        kind=kind,
        seconds=seconds,
        starts_at=starts or now - DAY,
        expires_at=expires,
    )
    session.add(row)
    session.commit()
    return row


def spend_all(session, user, grant, now=T0):
    """Use every second of `grant` (an entry with an allocation is settled: no backlog)."""
    entry = UsageEntry(
        user_id=user.id, email_hash=email_hash(user.email), seconds=grant.seconds, counted_at=now
    )
    session.add(entry)
    session.flush()
    session.add(UsageAllocation(usage_entry_id=entry.id, grant_id=grant.id, seconds=grant.seconds))
    session.commit()


def put_in_state(session, user, state, now=T0):
    """Give `user` the grants that put them in `state` at `now`. `past_due` and `lapsed` are not
    built here (no subscriptions yet); `in_state` handles them."""
    if state == "trial":
        add_grant(session, user, "trial", 3 * HOUR, expires=now + 13 * DAY, now=now)
    elif state == "pilot":
        add_grant(session, user, "pilot", 5 * HOUR, now=now)
    elif state == "active":
        add_grant(session, user, "plan_period", 20 * HOUR, expires=now + 20 * DAY, now=now)
    elif state == "active_hours_spent":
        plan = add_grant(session, user, "plan_period", 20 * HOUR, expires=now + 20 * DAY, now=now)
        spend_all(session, user, plan, now)
    elif state == "trial_used_up":
        trial = add_grant(session, user, "trial", 3 * HOUR, expires=now + 13 * DAY, now=now)
        spend_all(session, user, trial, now)
    elif state == "trial_ended":
        add_grant(
            session, user, "trial", 3 * HOUR, starts=now - 15 * DAY, expires=now - DAY, now=now
        )
    elif state == "pilot_used_up":
        pilot = add_grant(session, user, "pilot", 5 * HOUR, now=now)
        spend_all(session, user, pilot, now)
    elif state == "pack_only":
        add_grant(session, user, "pack", 10 * HOUR, expires=now + 90 * DAY, now=now)
    elif state == "admin_gift_only":
        add_grant(session, user, "admin_gift", 10 * HOUR, now=now)
    elif state in ("none", "admin", "past_due", "lapsed"):
        pass
    else:
        raise AssertionError(state)


def in_state(session, user, state, monkeypatch, now=T0):
    """Put `user` in `state`. The admin is a flag on the account; `past_due` and `lapsed` replace
    `status` (their data arrives in S8), everything else is real grants."""
    if state == "admin":
        user.is_admin = True
        session.commit()
    elif state in ("past_due", "lapsed"):

        def status(session_, user_, now_, *args, **kwargs):
            return entitlements.Status(state, 0, False, False)

        monkeypatch.setattr(entitlements, "status", status)
    put_in_state(session, user, state, now)


def gate_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


@pytest.fixture
def mode(monkeypatch):
    """`mode("enforce")` sets the mode the routes and the model functions read."""

    def set_mode(name: str):
        monkeypatch.setattr(feature_gates, "get_settings", lambda: settings_for(name))

    return set_mode


# ---- the setting ----


def test_the_default_is_off_and_the_value_is_checked():
    assert make_settings().feature_gates_mode == "off"
    assert make_settings(feature_gates_mode="").feature_gates_mode == "off"
    assert make_settings(feature_gates_mode=" Enforce ").feature_gates_mode == "enforce"
    with pytest.raises(ValueError, match="FEATURE_GATES_MODE"):
        make_settings(feature_gates_mode="on")


# ---- the table: every state of spec 2.2 against every gate in every mode ----


def test_the_table_covers_every_state_of_the_spec():
    assert set(STATES) == {
        "trial",
        "pilot",
        "active",
        "active_hours_spent",
        "admin",
        "past_due",
        "none",
        "trial_used_up",
        "trial_ended",
        "pilot_used_up",
        "pack_only",
        "admin_gift_only",
        "lapsed",
    }
    assert entitlements.OPEN_STATES == {"trial", "pilot", "active", "past_due"}


@pytest.mark.parametrize("state, is_open", STATES.items())
def test_the_pure_rule(session, owner, monkeypatch, state, is_open):
    """What `entitlements.can` answers, for every gate; `status` is shown in the failure."""
    in_state(session, owner, state, monkeypatch)
    for feature in GATES:
        decision = entitlements.gate(session, owner, feature, T0)
        assert decision.allowed is is_open, (state, feature, decision)
        assert entitlements.can(session, owner, feature, T0) is is_open


@pytest.mark.parametrize("mode_name", ["off", "shadow", "enforce"])
@pytest.mark.parametrize("state, is_open", STATES.items())
def test_every_state_against_every_gate_in_every_mode(
    session, owner, monkeypatch, caplog, state, is_open, mode_name
):
    in_state(session, owner, state, monkeypatch)
    for feature in GATES:
        caplog.clear()
        settings = settings_for(mode_name)
        with caplog.at_level(logging.INFO, logger=LOGGER):
            if mode_name == "enforce" and not is_open:
                with pytest.raises(Refused) as refused:
                    feature_gates.require(session, owner, feature, now=T0, settings=settings)
                assert (refused.value.status, refused.value.code) == (403, "plan_inactive"), feature
            else:  # off and shadow never refuse; enforce lets an open state through
                feature_gates.require(session, owner, feature, now=T0, settings=settings)
        lines = gate_lines(caplog)
        if mode_name == "shadow" and not is_open:  # exactly one "would have refused" line
            assert len(lines) == 1 and lines[0].startswith(
                f"feature_gates_shadow would have refused feature={feature} user={owner.id} state="
            ), (state, feature, lines)
        else:
            assert lines == [], (state, mode_name, feature, lines)


def test_an_unknown_feature_is_an_error_not_a_silent_open_or_closed_gate(session, owner):
    with pytest.raises(ValueError, match="unknown feature"):
        entitlements.can(session, owner, "export", T0)


def test_a_state_nobody_listed_is_closed():
    assert entitlements.state_allows("something_new") is False
    assert entitlements.state_allows("none", unlimited=True) is True


def test_the_refusal_says_nothing_about_a_pilot_or_an_address():
    for feature in GATES:
        text = feature_gates.plan_inactive_message(feature).lower()
        assert "pilot" not in text and "hello@" not in text and "@" not in text
        assert "plan or trial" in text and "read, search and export" in text


# ---- off changes nothing; shadow logs once and proceeds ----


def test_with_the_flag_off_nothing_is_read_and_nothing_is_logged(
    engine, session, owner, caplog, monkeypatch
):
    monkeypatch.setattr(
        entitlements, "gate", lambda *a, **k: pytest.fail("the gate was asked with the flag off")
    )
    statements = []
    event.listen(engine, "before_cursor_execute", lambda *a: statements.append(a[2]))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for feature in GATES:
            feature_gates.require(session, owner, feature, now=T0, settings=settings_for("off"))
    assert statements == [] and gate_lines(caplog) == []


def test_shadow_logs_ids_only_never_a_name_or_an_email(session, owner, caplog):
    owner.name = "Ada Lovelace"
    session.commit()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        feature_gates.require(
            session, owner, entitlements.ASK, now=T0, settings=settings_for("shadow")
        )
    [line] = gate_lines(caplog)
    assert line == f"feature_gates_shadow would have refused feature=ask user={owner.id} state=none"
    assert "@" not in line and "Ada" not in line and owner.email not in line


def test_shadow_that_cannot_check_logs_the_class_and_lets_the_call_go_on(
    session, owner, caplog, monkeypatch
):
    def broken(*args, **kwargs):
        raise RuntimeError(f"bound parameter {owner.email}")

    monkeypatch.setattr(entitlements, "gate", broken)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        feature_gates.require(
            session, owner, entitlements.ASK, now=T0, settings=settings_for("shadow")
        )
    [line] = gate_lines(caplog)
    assert line == f"feature_gates_shadow failed feature=ask user={owner.id} error=RuntimeError"
    assert owner.email not in line


def test_enforce_that_cannot_check_fails_closed(session, owner, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(entitlements, "gate", broken)
    with pytest.raises(RuntimeError):
        feature_gates.require(
            session, owner, entitlements.ASK, now=T0, settings=settings_for("enforce")
        )


# ---- Ask Beyric ----


def ask_runs(session):
    return session.scalar(select(func.count()).select_from(AiRun).where(AiRun.kind == "ask"))


def test_ask_is_refused_without_a_plan_and_the_model_is_never_called(
    api, session, template, model, owner, mode
):
    mode("enforce")
    ask_meeting(session, template, title="Pricing review")
    error = error_of(post_ask(api, "What did we decide about pricing?"), 403, "plan_inactive")
    assert error["message"] == feature_gates.plan_inactive_message("ask")
    assert model.calls == [] and ask_runs(session) == 0


def test_the_free_my_tasks_answer_is_gated_too_so_ask_is_one_switch(api, session, model, mode):
    mode("enforce")
    error_of(post_ask(api, "What are my open tasks?"), 403, "plan_inactive")


def test_a_bad_question_is_still_422_before_the_gate(api, model, mode):
    mode("enforce")
    error_of(post_ask(api, "   "), 422, "question_invalid")


@pytest.mark.parametrize("state", ["trial", "pilot", "active", "active_hours_spent", "past_due"])
def test_ask_works_in_every_open_state_under_enforce(
    api, session, template, model, owner, mode, monkeypatch, state
):
    mode("enforce")
    in_state(session, owner, state, monkeypatch, now=datetime.now(UTC))
    ask_meeting(session, template, title="Pricing review")
    assert post_ask(api, "What did we decide about pricing?").json()["kind"] == "answer"
    assert len(model.calls) == 1


def test_ask_in_shadow_answers_and_logs_one_line(api, session, template, model, mode, caplog):
    mode("shadow")
    ask_meeting(session, template, title="Pricing review")
    with caplog.at_level(logging.INFO, logger=LOGGER):
        response = post_ask(api, "What did we decide about pricing?")
    assert response.status_code == 200 and response.json()["kind"] == "answer"
    assert len(model.calls) == 1
    assert [line.split(" user=")[0] for line in gate_lines(caplog)] == [
        "feature_gates_shadow would have refused feature=ask"
    ]


def test_ask_with_the_flag_off_is_as_before(api, session, template, model, mode, caplog):
    mode("off")
    ask_meeting(session, template, title="Pricing review")
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert post_ask(api, "What did we decide about pricing?").status_code == 200
    assert gate_lines(caplog) == []


def test_the_scope_line_and_chips_are_not_gated(api, session, template, model, mode):
    mode("enforce")
    ask_meeting(session, template)
    assert api.get("/api/ask/scope").status_code == 200  # no model call


# ---- the reader-only teammate ----


def test_a_teammate_with_no_plan_reads_shared_notes_but_cannot_ask(
    app, api, session, template, owner, teammate, model, mode
):
    mode("enforce")
    shared = ask_meeting(session, template, title="Pricing review")
    share_with_team(session, shared, owner)
    reader = sign_in(app, TEAMMATE_EMAIL)
    assert reader.get(f"/api/meetings/{shared.id}").status_code == 200  # reads what is shared
    assert reader.get("/api/meetings").status_code == 200
    assert reader.get(f"/api/meetings/{shared.id}/export?format=docx").status_code == 200
    error_of(post_ask(reader, "What did we decide about pricing?"), 403, "plan_inactive")
    assert model.calls == []
    # the day the friend subscribes, Ask answers from what they may read, the shared meeting
    add_grant(session, teammate, "plan_period", 20 * HOUR, expires=datetime.now(UTC) + 20 * DAY)
    answered = post_ask(reader, "What did we decide about pricing?").json()
    assert answered["kind"] == "answer"
    assert answered["points"][0]["cites"][0]["meeting_id"] == shared.id


# ---- new translations ----


def translation_rows(session):
    return session.scalar(select(func.count()).select_from(MeetingTranslation))


def test_a_new_translation_is_refused_without_a_plan_and_nothing_is_queued(
    api, session, template, mode
):
    mode("enforce")
    meeting = translatable(session, template)
    error = error_of(ask_translation(api, meeting), 403, "plan_inactive")
    assert error["message"] == feature_gates.plan_inactive_message("translate")
    assert translation_rows(session) == 0
    # the refusal released the account lock: the same person can be answered again at once
    error_of(ask_translation(api, meeting), 403, "plan_inactive")


@pytest.mark.parametrize("state", ["trial", "pilot", "active", "active_hours_spent", "past_due"])
def test_a_new_translation_is_queued_in_every_open_state_under_enforce(
    api, session, template, owner, mode, monkeypatch, state
):
    mode("enforce")
    in_state(session, owner, state, monkeypatch, now=datetime.now(UTC))
    assert ask_translation(api, translatable(session, template)).status_code == 202


def test_a_cached_and_a_running_translation_stay_open_after_the_plan_ends(
    api, session, template, owner, mode
):
    mode("enforce")
    now = datetime.now(UTC)
    trial = add_grant(session, owner, "trial", 3 * HOUR, expires=now + 13 * DAY, now=now)
    done = translatable(session, template)
    running = translatable(session, template)
    assert ask_translation(api, done).status_code == 202
    work(session)  # `done` is ready
    assert ask_translation(api, running).status_code == 202  # queued, not worked yet
    session.delete(trial)  # the plan ends: no grant, state none
    session.commit()
    assert entitlements.can(session, owner, entitlements.TRANSLATE, now) is False
    assert ask_translation(api, done).status_code == 200  # the cached one is served
    assert api.get(translation_url(done)).status_code == 200  # reading is never gated
    assert ask_translation(api, running).status_code == 202  # already being made: unchanged
    other = translatable(session, template)
    error_of(ask_translation(api, other), 403, "plan_inactive")  # only a NEW one is refused


def test_a_translation_queued_while_allowed_is_finished_by_the_worker_later(
    api, session, template, owner, mode
):
    """The gate is at the request: the worker is not gated, a queued translation was allowed."""
    mode("enforce")
    now = datetime.now(UTC)
    trial = add_grant(session, owner, "trial", 3 * HOUR, expires=now + 13 * DAY, now=now)
    meeting = translatable(session, template)
    assert ask_translation(api, meeting).status_code == 202
    session.delete(trial)
    session.commit()
    work(session)
    assert api.get(translation_url(meeting)).json()["status"] == "ready"


def test_a_new_translation_in_shadow_is_queued_and_logs_one_line(
    api, session, template, mode, caplog
):
    mode("shadow")
    meeting = translatable(session, template)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert ask_translation(api, meeting).status_code == 202
    assert len(gate_lines(caplog)) == 1 and "feature=translate" in gate_lines(caplog)[0]


def share_body(language):
    return {"recipients": [{"email": "client@example.test"}], "language": language}


def test_sending_notes_in_french_is_gated_because_it_makes_a_translation(
    api, session, template, mode
):
    """`shares.create_share` calls `translation.request_translation`: no way round the gate."""
    mode("enforce")
    meeting = translatable(session, template)
    error_of(api.post(f"/api/meetings/{meeting.id}/shares", share_body("fr")), 403, "plan_inactive")
    assert translation_rows(session) == 0
    assert session.scalar(select(func.count()).select_from(MeetingShare)) == 0
    # English needs no translation: sending notes as they are stays open
    assert api.post(f"/api/meetings/{meeting.id}/shares", share_body("en")).status_code == 202


# ---- rewriting notes ----


def rewritten_notes() -> str:
    headings = ("Summary", "Key points", "Decisions", "Action items", "Open questions")
    return "\n\n".join(f"## {h}\nRewritten" for h in (*headings, "Please check"))


def other_template(session, template):
    other = Template(owner_user_id=template.owner_user_id, name="Short", instructions="Be brief.")
    session.add(other)
    session.commit()
    return other


def test_rewriting_is_refused_without_a_plan_and_the_notes_are_kept(
    api, session, template, fake_openai, mode
):
    mode("enforce")
    meeting = ready(session, template)
    before = (meeting.notes, meeting.template_id)
    other = other_template(session, template)
    error = error_of(
        api.post(f"/api/meetings/{meeting.id}/regenerate", {"template_id": other.id}),
        403,
        "plan_inactive",
    )
    assert error["message"] == feature_gates.plan_inactive_message("rewrite")
    assert fake_openai.chat_calls == []
    session.refresh(meeting)
    assert (meeting.notes, meeting.template_id) == before
    assert session.scalar(select(func.count()).select_from(AiRun)) == 0


def test_the_legacy_page_route_cannot_go_round_the_rewrite_gate(
    client, session, template, fake_openai, mode
):
    mode("enforce")
    meeting = ready(session, template)
    before = (meeting.notes, meeting.template_id)
    other = other_template(session, template)
    response = client.post(
        f"/meetings/{meeting.id}/regenerate",
        data={"template_id": str(other.id), "csrf_token": token_from(client.get("/").text)},
    )
    assert response.status_code == 403
    assert feature_gates.plan_inactive_message("rewrite") in response.text
    assert fake_openai.chat_calls == []
    session.refresh(meeting)
    assert (meeting.notes, meeting.template_id) == before


def test_the_gate_is_inside_the_service_so_any_caller_is_covered(
    session, owner, template, fake_openai, mode
):
    mode("enforce")
    meeting = ready(session, template)
    with pytest.raises(Refused) as refused:
        services.regenerate_notes(session, owner, meeting, template.id, fake_openai)
    assert (refused.value.status, refused.value.code) == (403, "plan_inactive")
    assert fake_openai.chat_calls == []


@pytest.mark.parametrize("state", ["trial", "pilot", "active", "active_hours_spent", "past_due"])
def test_rewriting_works_in_every_open_state_under_enforce(
    api, session, template, owner, fake_openai, mode, monkeypatch, state
):
    mode("enforce")
    in_state(session, owner, state, monkeypatch, now=datetime.now(UTC))
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
    other = other_template(session, template)
    response = api.post(f"/api/meetings/{meeting.id}/regenerate", {"template_id": other.id})
    assert response.status_code == 200 and "Rewritten" in response.json()["notes"]


def test_rewriting_in_shadow_rewrites_and_logs_one_line_and_off_logs_none(
    api, session, template, fake_openai, mode, caplog
):
    meeting = ready(session, template)
    other = other_template(session, template)
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
    mode("off")
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert (
            api.post(
                f"/api/meetings/{meeting.id}/regenerate", {"template_id": other.id}
            ).status_code
            == 200
        )
    assert gate_lines(caplog) == []
    mode("shadow")
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert (
            api.post(
                f"/api/meetings/{meeting.id}/regenerate", {"template_id": template.id}
            ).status_code
            == 200
        )
    assert len(gate_lines(caplog)) == 1 and "feature=rewrite" in gate_lines(caplog)[0]
    assert len(fake_openai.chat_calls) == 2


def test_a_meeting_not_ready_is_409_before_the_gate_and_logs_nothing_in_shadow(
    api, session, template, mode, caplog
):
    waiting = add_meeting(session, template)
    mode("enforce")
    error_of(
        api.post(f"/api/meetings/{waiting.id}/regenerate", {"template_id": template.id}),
        409,
        "not_ready",
    )
    mode("shadow")
    with caplog.at_level(logging.INFO, logger=LOGGER):
        error_of(
            api.post(f"/api/meetings/{waiting.id}/regenerate", {"template_id": template.id}),
            409,
            "not_ready",
        )
    assert gate_lines(caplog) == []


# ---- what stays open, for a person with no plan, under enforce ----


def test_reading_export_review_and_the_rest_stay_open_with_no_plan(
    api, session, template, owner, mode
):
    mode("enforce")
    meeting = extracted(session, template)
    assert api.get("/api/meetings").status_code == 200
    assert api.get(f"/api/meetings/{meeting.id}").status_code == 200
    assert api.get(f"/api/meetings/{meeting.id}/export?format=docx").status_code == 200
    assert api.get("/api/decisions").status_code == 200
    assert api.get("/api/review").status_code == 200
    item = api.get("/api/review").json()["items"][0]
    # Review's "add the corrected spelling" calls no model, so it is never gated (spec 2.3)
    added = api.post(f"/api/review/{item['id']}/vocabulary", {"term": "Metrika"})
    assert added.status_code == 201


# ---- a team admin rewrites or translates a moved meeting: their OWN plan decides (spec 2.3) ----


@pytest.fixture
def moved(session, owner, template, teammate):
    """A meeting the owner recorded and moved to their team, with `teammate` its admin."""
    team = teams.team_of(session, owner.id)
    member = session.scalar(select(TeamMember).where(TeamMember.user_id == teammate.id))
    member.role = "admin"
    meeting = translatable(session, template)
    meeting.owner_team_id = team.id
    session.commit()
    return meeting


def test_an_admin_without_a_plan_is_refused_even_when_the_recorder_has_one(
    app, session, owner, teammate, template, moved, fake_openai, mode
):
    mode("enforce")
    add_grant(session, owner, "plan_period", 20 * HOUR, expires=datetime.now(UTC) + 20 * DAY)
    admin = sign_in(app, TEAMMATE_EMAIL)
    error_of(
        admin.post(f"/api/meetings/{moved.id}/regenerate", {"template_id": template.id}),
        403,
        "plan_inactive",
    )
    error_of(ask_translation(admin, moved), 403, "plan_inactive")
    assert fake_openai.chat_calls == [] and translation_rows(session) == 0


def test_an_admin_with_a_plan_may_rewrite_when_the_recorder_has_none(
    app, session, owner, teammate, template, moved, fake_openai, mode
):
    mode("enforce")
    add_grant(session, teammate, "plan_period", 20 * HOUR, expires=datetime.now(UTC) + 20 * DAY)
    fake_openai.notes = rewritten_notes()
    admin = sign_in(app, TEAMMATE_EMAIL)
    response = admin.post(f"/api/meetings/{moved.id}/regenerate", {"template_id": template.id})
    assert response.status_code == 200 and "Rewritten" in response.json()["notes"]
    assert ask_translation(admin, moved).status_code == 202


# ---- a failed or a stale translation asked for again is a new model call: gated ----


def test_try_again_on_a_failed_translation_and_re_translating_after_a_rewrite_are_gated(
    api, session, template, owner, mode
):
    mode("enforce")
    now = datetime.now(UTC)
    trial = add_grant(session, owner, "trial", 3 * HOUR, expires=now + 13 * DAY, now=now)
    failed, stale = translatable(session, template), translatable(session, template)
    assert ask_translation(api, failed).status_code == 202
    work(session, FakeModel(lambda texts, n: ["x"]))  # `failed` fails its check
    assert ask_translation(api, stale).status_code == 202
    work(session)  # `stale` is ready...
    stale.notes = stale.notes.replace("14 March", "1 May")  # ...then its notes change
    session.commit()
    assert api.get(translation_url(failed)).json()["status"] == "failed"
    assert api.get(translation_url(stale)).json()["stale"] is True
    session.delete(trial)  # the plan ends
    session.commit()
    error_of(ask_translation(api, failed), 403, "plan_inactive")
    error_of(ask_translation(api, stale), 403, "plan_inactive")
    assert api.get(translation_url(failed)).json()["status"] == "failed"  # untouched, still read
    assert api.get(translation_url(stale)).json()["status"] == "ready"


# ---- shadow: a real database error inside the savepoint costs the request nothing ----


def test_a_database_error_inside_the_shadow_check_leaves_the_request_intact(
    api, session, template, mode, caplog, monkeypatch
):
    """`request_translation` holds the account lock and a row lock when the gate runs. A SQL error
    in the check rolls back only the savepoint: the translation is still queued, and the line says
    the class only."""
    mode("shadow")

    def broken(session_, user_, now_, *args, **kwargs):
        session_.execute(text("select 1/0"))  # a real error from the database, not a stub

    monkeypatch.setattr(entitlements, "status", broken)
    meeting = translatable(session, template)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert ask_translation(api, meeting).status_code == 202
    [line] = gate_lines(caplog)
    assert line.startswith("feature_gates_shadow failed feature=translate user=")
    assert "error=" in line and "zero" not in line.lower() and "division" not in line.lower()
    assert api.get(translation_url(meeting)).json()["status"] == "translating"
