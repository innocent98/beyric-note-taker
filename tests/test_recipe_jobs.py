# ruff: noqa: F811
"""Asking for a draft, the queue, the worker and the daily cap (`app/recipes/service.py`). Follows
`tests/test_translation_jobs.py`; the model is the fake of `tests/test_recipe_generate.py`."""

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import feature_gates, teams
from app.models import AiRun, Document, DocumentMeeting, Recipe, TeamBrief
from app.recipes import service as svc
from app.services import Refused
from tests.test_recipe_generate import TRANSCRIPT, FakeModel, para, reply
from tests.test_web import (  # noqa: F401  (fixtures used by name)
    OTHER_EMAIL,
    add_meeting,
    make_settings,
    make_user,
    owner,
    stranger,
    template,
)

T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

STARTERS = {
    "follow_up_email": SimpleNamespace(
        key="follow_up_email",
        name="Follow-up email",
        instruction="Write a friendly follow-up.",
        output_type="email",
        scope="one",
        uses_brief=True,
    ),
    "status_report": SimpleNamespace(
        key="status_report",
        name="Status report",
        instruction="Summarise progress.",
        output_type="document",
        scope="many",
        uses_brief=False,
    ),
}


@pytest.fixture(autouse=True)
def starters(monkeypatch):
    monkeypatch.setattr(svc, "starter_for", STARTERS.get)


@pytest.fixture
def limit(monkeypatch):
    def set_limit(n):
        settings = make_settings(recipe_daily_limit=n)
        monkeypatch.setattr(svc, "get_settings", lambda: settings)

    return set_limit


def ready_meeting(session, template, **fields):
    values = {
        "status": "ready",
        "step": "done",
        "title": "Onboarding call",
        "transcript": TRANSCRIPT,
        "speaker_transcript": TRANSCRIPT,
        "notes": "## Summary\nOnboarding for 50,000 naira.",
    }
    return add_meeting(session, template, **{**values, **fields})


@pytest.fixture
def meeting(session, template):
    return ready_meeting(session, template)


def ask(session, user, meeting_ids, ref="follow_up_email", **kw):
    kw.setdefault("now", T0)
    return svc.request_draft(session, user, recipe_ref=ref, meeting_ids=meeting_ids, **kw)


def runs(session):
    return list(session.scalars(select(AiRun).where(AiRun.kind == "recipe").order_by(AiRun.id)))


def good(blocks=None):
    return FakeModel(reply(blocks or [para("Hi Daniel, thanks for today.")]))


def test_request_writes_pending_ledger_and_queues(session, owner, meeting):
    doc = ask(session, owner, [meeting.id], options={"tone": "friendly"})
    assert (doc.status, doc.attempts, doc.blocks, doc.output_type) == ("writing", 0, [], "email")
    assert doc.recipe_key == "follow_up_email" and doc.recipe_id is None
    assert doc.owner_user_id == owner.id and doc.team_id == teams.ensure_team(session, owner).id
    assert doc.options == {"tone": "friendly"} and doc.left_out_count == 0
    assert svc._meeting_set(session, doc.id) == {meeting.id}
    [run] = runs(session)
    assert (run.status, run.user_id, run.meeting_id, run.prompt_version) == (
        "pending",
        owner.id,
        meeting.id,
        "recipe-v1",
    )
    assert run.created_at == T0 and doc.ledger_run_id == run.id
    assert svc.daily_count(session, owner.id, T0) == 1


def test_double_submit_creates_one_draft(session, owner, meeting):
    first = ask(session, owner, [meeting.id], options={"tone": "friendly"})
    again = ask(
        session, owner, [meeting.id], options={"tone": "friendly"}, now=T0 + timedelta(seconds=3)
    )
    assert again.id == first.id
    assert session.scalar(select(Document.id).where(Document.id != first.id)) is None
    assert len(runs(session)) == 1
    # a different choice, or a different recipe, is a different draft
    other = ask(session, owner, [meeting.id], options={"tone": "formal"})
    assert other.id != first.id
    # and once it is no longer writing (or old), the same request makes a new draft
    first.status = "ready"
    session.commit()
    assert ask(session, owner, [meeting.id], options={"tone": "friendly"}).id not in (
        first.id,
        other.id,
    )


def test_cap_counts_failed_and_regenerated(session, owner, meeting, limit):
    limit(3)
    first = ask(session, owner, [meeting.id], options={"n": 1})
    svc.process_next(session, T0, client=FakeModel(RuntimeError("down")))  # a failed attempt
    assert session.get(Document, first.id).status == "failed"
    ask(session, owner, [meeting.id], options={"n": 2})  # a regeneration
    svc.process_next(session, T0 + timedelta(seconds=5), client=good())
    ask(session, owner, [meeting.id], options={"n": 3})
    assert svc.daily_count(session, owner.id, T0) == 3
    with pytest.raises(Refused) as error:
        ask(session, owner, [meeting.id], options={"n": 4})
    assert (error.value.status, error.value.code) == (429, "recipe_limit")
    assert "3 documents today" in error.value.message
    assert (
        len(runs(session)) == 3
        and session.scalar(select(Document.id).where(Document.options == {"n": 4})) is None
    )
    # a day later the slots are free again
    assert ask(session, owner, [meeting.id], options={"n": 5}, now=T0 + timedelta(hours=25))


def test_a_refusal_before_the_model_is_not_counted(
    session, owner, stranger, template, meeting, limit
):
    limit(5)
    theirs = ready_meeting(session, make_template(session, stranger))
    bad = [
        (dict(meeting_ids=[theirs.id]), 404, "not_found"),
        (dict(meeting_ids=[999999]), 404, "not_found"),
        (dict(meeting_ids=[]), 422, "meetings_invalid"),
        (dict(meeting_ids=[meeting.id, meeting.id + 1]), 422, "meetings_invalid"),
        (dict(meeting_ids=[meeting.id], ref="nope"), 404, "not_found"),
        (dict(meeting_ids=[meeting.id], team_id=99999), 404, "not_found"),
    ]
    for kw, status, code in bad:
        kw = {
            "meeting_ids": kw["meeting_ids"],
            **{k: v for k, v in kw.items() if k != "meeting_ids"},
        }
        with pytest.raises(Refused) as error:
            ask(session, owner, **kw)
        assert (error.value.status, error.value.code) == (status, code), kw
    pending = ready_meeting(session, template, status="processing", notes=None, transcript=None)
    with pytest.raises(Refused) as error:
        ask(session, owner, [pending.id])
    assert (error.value.status, error.value.code) == (409, "not_ready")
    assert runs(session) == [] and session.scalar(select(Document.id)) is None
    assert svc.daily_count(session, owner.id, T0) == 0


def make_template(session, user):
    from app.models import Template

    t = Template(owner_user_id=user.id, name="General", instructions="x")
    session.add(t)
    session.commit()
    return t


def test_the_make_gate_refuses_before_anything_is_written(session, owner, meeting, monkeypatch):
    settings = make_settings(feature_gates_mode="enforce")
    monkeypatch.setattr(feature_gates, "get_settings", lambda: settings)
    with pytest.raises(Refused) as error:
        ask(session, owner, [meeting.id])
    assert (error.value.status, error.value.code) == (403, "plan_inactive")
    assert error.value.message == feature_gates.plan_inactive_message("make")
    assert runs(session) == [] and session.scalar(select(Document.id)) is None


def test_cap_race_two_requests_one_slot(engine, session, owner, meeting, limit):
    limit(1)
    teams.ensure_team(session, owner)  # every account has a team from sign-up
    session.commit()
    barrier = threading.Barrier(2)
    outcomes: list = []
    lock = threading.Lock()
    user_id, meeting_id = owner.id, meeting.id

    def attempt(n):
        with Session(engine) as mine:
            user = mine.get(type(owner), user_id)
            barrier.wait()
            try:
                doc = svc.request_draft(
                    mine,
                    user,
                    recipe_ref="follow_up_email",
                    meeting_ids=[meeting_id],
                    options={"n": n},  # different choices: not a double submit
                    now=T0,
                )
                result = ("ok", doc.id)
            except Refused as exc:
                result = ("refused", exc.code)
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=attempt, args=(n,)) for n in (1, 2)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(o[0] for o in outcomes) == ["ok", "refused"]
    assert ("refused", "recipe_limit") in outcomes
    session.expire_all()
    assert len(runs(session)) == 1 and len(list(session.scalars(select(Document)))) == 1


def test_double_submit_race_makes_one_draft(engine, session, owner, meeting):
    teams.ensure_team(session, owner)
    session.commit()
    barrier = threading.Barrier(2)
    ids: list = []
    lock = threading.Lock()
    user_id, meeting_id = owner.id, meeting.id

    def attempt():
        with Session(engine) as mine:
            user = mine.get(type(owner), user_id)
            barrier.wait()
            doc = svc.request_draft(
                mine, user, recipe_ref="follow_up_email", meeting_ids=[meeting_id], now=T0
            )
            with lock:
                ids.append(doc.id)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert len(ids) == 2 and ids[0] == ids[1]
    session.expire_all()
    assert len(runs(session)) == 1


def test_worker_claims_fills_and_settles_ledger(session, owner, meeting):
    team = teams.ensure_team(session, owner)
    session.add(
        TeamBrief(team_id=team.id, text="Setup costs 50,000 naira.", updated_by_user_id=owner.id)
    )
    session.commit()
    doc = ask(session, owner, [meeting.id])
    model = good(
        [para("Total is 50,000 naira.", [{"kind": "line", "meeting_id": meeting.id, "line": 2}])]
    )
    assert svc.process_next(session, T0 + timedelta(seconds=1), client=model) == doc.id
    session.expire_all()
    doc = session.get(Document, doc.id)
    assert (doc.status, doc.attempts, doc.failure_code, doc.model) == (
        "ready",
        1,
        None,
        "gpt-6-luna",
    )
    assert doc.prompt_version == "recipe-v1" and doc.title == "Thanks for today"
    assert doc.blocks[0]["text"] == "Total is 50,000 naira."
    assert doc.blocks[0]["sources"][0]["line"] == 2 and doc.blocks[0]["sources"][0]["quote"]
    assert len(doc.source_hash) == 64 and doc.ledger_run_id is None
    [run] = runs(session)
    assert (run.status, run.input_tokens, run.output_tokens, run.error) == ("ok", 1500, 300, None)
    assert "Setup costs 50,000 naira." in model.calls[0]["messages"][1]["content"]  # the brief
    # nothing left to do
    assert svc.process_next(session, T0 + timedelta(seconds=2), client=good()) is None


def test_the_brief_is_not_sent_when_the_person_switches_it_off(session, owner, meeting):
    team = teams.ensure_team(session, owner)
    session.add(TeamBrief(team_id=team.id, text="PRICE-LIST-TEXT", updated_by_user_id=owner.id))
    session.commit()
    ask(session, owner, [meeting.id], use_brief=False)
    model = good()
    svc.process_next(session, T0, client=model)
    assert "PRICE-LIST-TEXT" not in model.calls[0]["messages"][1]["content"]


def test_failed_run_sets_failure_code_and_counts(session, owner, meeting):
    doc = ask(session, owner, [meeting.id])
    svc.process_next(session, T0, client=FakeModel(("not json", "stop")))
    session.expire_all()
    doc = session.get(Document, doc.id)
    assert (doc.status, doc.failure_code, doc.blocks) == ("failed", "model_reply", [])
    [run] = runs(session)
    assert (run.status, run.input_tokens) == (
        "failed",
        1500,
    ) and run.error == "JSONDecodeError"  # the underlying class
    assert svc.daily_count(session, owner.id, T0) == 1
    # an unexpected error is a plain code, not its text
    ask(session, owner, [meeting.id], options={"n": 2})
    svc.process_next(session, T0, client=FakeModel(RuntimeError("secret words")))
    assert (
        session.scalars(select(Document).order_by(Document.id.desc())).first().failure_code
        == "model_error"
    )


def test_a_draft_that_never_reached_the_model_is_not_counted(session, owner, template):
    empty = ready_meeting(session, template, transcript="", speaker_transcript="", notes=" ")
    # its text is blank, so the request itself is refused as not ready ...
    with pytest.raises(Refused):
        ask(session, owner, [empty.id])
    # ... and one that went blank after the request fails with `empty` and gives its slot back
    ok = ready_meeting(session, template)
    doc = ask(session, owner, [ok.id])
    ok.notes, ok.transcript, ok.speaker_transcript = "", "", ""
    session.commit()
    model = good()
    svc.process_next(session, T0, client=model)
    session.expire_all()
    assert session.get(Document, doc.id).failure_code == "empty" and model.calls == []
    assert runs(session) == [] and svc.daily_count(session, owner.id, T0) == 0


def test_dead_worker_is_reclaimed(session, owner, meeting):
    doc = ask(session, owner, [meeting.id])
    # a worker claimed it and died: started, never finished
    doc.started_at, doc.attempts = T0, 1
    session.commit()
    assert svc.process_next(session, T0 + timedelta(minutes=1), client=good()) is None  # still held
    later = T0 + svc.STALE_RUNNING + timedelta(seconds=1)
    assert svc.process_next(session, later, client=good()) == doc.id
    session.expire_all()
    doc = session.get(Document, doc.id)
    assert (doc.status, doc.attempts) == ("ready", 2)
    assert len(runs(session)) == 1 and runs(session)[0].status == "ok"  # one request, one row


def test_gave_up_after_max_attempts(session, owner, meeting):
    doc = ask(session, owner, [meeting.id])
    doc.started_at, doc.attempts = T0, svc.MAX_ATTEMPTS
    session.commit()
    model = good()
    later = T0 + svc.STALE_RUNNING + timedelta(seconds=1)
    assert svc.process_next(session, later, client=model) is None
    session.expire_all()
    doc = session.get(Document, doc.id)
    assert (doc.status, doc.failure_code) == ("failed", "gave_up") and model.calls == []
    assert runs(session)[0].status == "failed"  # counted: the dead worker may have called


def test_a_discarded_draft_is_not_brought_back_but_its_call_is_settled(session, owner, meeting):
    doc = ask(session, owner, [meeting.id])
    doc_id = doc.id
    seen = []

    def discard_meanwhile(kwargs, n):
        with Session(session.get_bind()) as other:
            other.execute(__import__("sqlalchemy").delete(Document).where(Document.id == doc_id))
            other.commit()
        seen.append(n)
        return reply([para("Hi")])

    svc.process_next(session, T0, client=FakeModel(discard_meanwhile))
    session.expire_all()
    assert seen == [1] and session.get(Document, doc_id) is None
    assert [r.status for r in runs(session)] == ["ok"]


def test_meeting_left_out_when_unreadable_is_recorded_on_draft(
    session, owner, stranger, template, meeting
):
    mine2 = ready_meeting(session, template, title="Second")
    theirs = ready_meeting(session, make_template(session, stranger), title="Not mine")
    doc = ask(session, owner, [meeting.id, theirs.id, mine2.id], ref="status_report")
    assert doc.left_out_count == 1
    assert svc._meeting_set(session, doc.id) == {meeting.id, mine2.id}
    model = good()
    svc.process_next(session, T0, client=model)
    sent = model.calls[0]["messages"][1]["content"]
    assert "Not mine" not in sent and "Second" in sent and "Onboarding call" in sent
    assert "[L1]" not in sent  # a cross-meeting recipe reads no full transcript
    session.expire_all()
    assert session.get(Document, doc.id).left_out_count == 1
    # none readable at all: the draft is refused, not made empty
    with pytest.raises(Refused) as error:
        ask(session, owner, [theirs.id], ref="status_report")
    assert error.value.status == 404


def test_a_meeting_unreadable_by_the_time_the_worker_runs_is_left_out(
    session, owner, template, meeting
):
    second = ready_meeting(session, template, title="Gone")
    doc = ask(session, owner, [meeting.id, second.id], ref="status_report")
    session.delete(second)
    session.commit()
    model = good()
    svc.process_next(session, T0, client=model)
    sent = model.calls[0]["messages"][1]["content"]
    assert "Gone" not in sent
    session.expire_all()
    assert session.get(Document, doc.id).status == "ready"


def test_a_recipe_row_is_used_by_its_owner_only(session, owner, stranger, meeting):
    mine = Recipe(owner_user_id=owner.id, name="Mine", instruction="Do it", output_type="document")
    theirs = Recipe(
        owner_user_id=stranger.id, name="Theirs", instruction="Do it", output_type="email"
    )
    gone = Recipe(
        owner_user_id=owner.id, name="Old", instruction="x", output_type="email", archived_at=T0
    )
    session.add_all([mine, theirs, gone])
    session.commit()
    doc = ask(session, owner, [meeting.id], ref=mine.id)
    assert (doc.recipe_id, doc.recipe_key, doc.title, doc.output_type) == (
        mine.id,
        None,
        "Mine",
        "document",
    )
    for ref in (theirs.id, gone.id, 999999):
        with pytest.raises(Refused) as error:
            ask(session, owner, [meeting.id], ref=ref)
        assert error.value.status == 404
    model = good()
    svc.process_next(session, T0, client=model)
    assert "Do it" in model.calls[0]["messages"][1]["content"]


def test_the_worker_thread_starts_and_stops_with_the_worker(monkeypatch):
    from app import worker

    started = threading.Event()
    monkeypatch.setattr(svc, "run_loop", lambda stop, **kw: (started.set(), stop.wait(5)))
    stop = threading.Event()
    thread = worker.start_recipe_thread(stop)
    assert started.wait(5) and thread.name == "recipes" and thread.daemon
    stop.set()
    thread.join(5)
    assert not thread.is_alive()


def test_run_loop_survives_a_failing_pass_and_stops(monkeypatch):
    calls = []

    class Factory:
        def __call__(self):
            calls.append(1)
            raise RuntimeError("db down")

    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda seconds: stop.set() if len(calls) >= 2 else None)
    svc.run_loop(stop, session_factory=Factory(), idle_seconds=0)
    assert len(calls) >= 2 and DocumentMeeting is not None


def test_a_person_who_left_the_team_or_a_recipe_archived_meanwhile_fails_before_the_model(
    session, owner, stranger, meeting
):
    from app.models import Team, TeamMember

    team = Team(name="Acme", owner_user_id=stranger.id)
    session.add(team)
    session.flush()
    member = TeamMember(team_id=team.id, user_id=owner.id, role="member")
    session.add_all([TeamMember(team_id=team.id, user_id=stranger.id, role="owner"), member])
    session.add(TeamBrief(team_id=team.id, text="SECRET-PRICES", updated_by_user_id=stranger.id))
    recipe = Recipe(
        owner_user_id=stranger.id,
        team_id=team.id,
        name="Team recipe",
        instruction="x",
        output_type="document",
    )
    session.add(recipe)
    session.commit()
    first = ask(session, owner, [meeting.id], team_id=team.id)
    second = ask(session, owner, [meeting.id], ref=recipe.id, team_id=team.id, options={"n": 2})
    # the person leaves the team, and the second draft's recipe is archived, before the worker runs
    session.delete(member)
    recipe.archived_at = T0
    session.commit()
    model = good()
    svc.process_next(session, T0, client=model)
    svc.process_next(session, T0, client=model)
    session.expire_all()
    assert [session.get(Document, d).failure_code for d in (first.id, second.id)] == [
        "access_gone",
        "access_gone",
    ]
    assert model.calls == [] and runs(session) == []  # nothing was sent; nothing counts
    # still a member, but the recipe was archived: recipe_gone
    session.add(TeamMember(team_id=team.id, user_id=owner.id, role="member"))
    session.commit()
    third = ask(session, owner, [meeting.id], team_id=team.id, options={"n": 3})
    svc.process_next(session, T0, client=model)
    assert session.get(Document, third.id).status == "ready"  # a starter needs no recipe row


def test_a_person_who_left_the_team_gets_no_hint_of_its_brief_from_the_audit(
    session, owner, stranger, meeting
):
    from app.models import Team, TeamMember

    team = Team(name="Acme", owner_user_id=stranger.id)
    session.add(team)
    session.flush()
    member = TeamMember(team_id=team.id, user_id=owner.id, role="member")
    session.add_all([TeamMember(team_id=team.id, user_id=stranger.id, role="owner"), member])
    session.add(
        TeamBrief(
            team_id=team.id, text="Package price 777,000 naira.", updated_by_user_id=stranger.id
        )
    )
    session.commit()
    doc = ask(session, owner, [meeting.id], team_id=team.id)
    svc.process_next(session, T0, client=good([para("The package is 777,000 naira.")]))
    session.refresh(doc)
    assert svc.reading(session, owner, doc).flags == []  # supported by the brief while on the team
    session.delete(member)
    session.commit()
    session.refresh(doc)
    seen = svc.reading(session, owner, doc)
    assert [f.text for f in seen.flags] == ["777,000 naira"]  # no longer explained by the brief
