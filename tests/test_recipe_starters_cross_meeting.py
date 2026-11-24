# ruff: noqa: F811
"""The three real cross-meeting starters of `app/recipes/library.py` (status report, client
briefing, revision plan), through the picker and the worker with a fake model (plan T15, spec 5.3b): each
resolves the picker's output, is made from each meeting's notes, decisions, tasks and open questions
and no full transcript, and its sources link to the meetings. The stand-in recipe tests are in
`tests/test_recipe_cross_meeting.py`."""

from datetime import UTC, datetime

import pytest

from app.models import Document, Series
from app.recipes import blocks as blocks_mod
from app.recipes import library, picker
from app.recipes import service as svc
from app.recipes.picker_spec import PickerSpec
from tests.test_recipe_cross_meeting import (  # noqa: F401  (fixtures used by name)
    SECRET,
    daniel,
    held,
    source,
    team,
    two,
)
from tests.test_recipe_generate import FakeModel, para, reply, system_and_user
from tests.test_web import owner, template  # noqa: F401

T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
KEYS = ("status_report", "client_briefing", "revision_plan")


def test_the_three_starters_read_several_meetings():
    for key in KEYS:
        starter = library.starter_by_key(key)
        assert starter is not None and starter.scope == "many", key
        assert svc.starter_for(key) is starter


@pytest.fixture
def series_of_three(session, owner, template, team, two):
    """The two shared meetings of `two` plus a private one, all in one recurring meeting."""
    found = Series(team_id=team.id, owner_user_id=owner.id, title="Monday planning")
    session.add(found)
    session.commit()
    hidden = held(session, template, team, 5, shared=False, title="Private chat")
    for meeting in [m for m, _, _ in two] + [hidden]:
        meeting.series_id = found.id
    session.commit()
    return found


@pytest.mark.parametrize("key", KEYS)
def test_a_starter_resolves_the_picker_and_is_made_from_notes_decisions_tasks_and_questions(
    key, session, daniel, two, series_of_three
):
    (m0, d0, t0), (m1, d1, t1) = two
    spec = PickerSpec(series_id=series_of_three.id)
    resolved = picker.resolve_meetings(session, daniel, spec)
    assert {m.id for m in resolved.meetings} == {m0.id, m1.id} and resolved.left_out_count == 1
    doc = svc.request_draft(
        session,
        daniel,
        recipe_ref=key,
        meeting_ids=[m.id for m in resolved.meetings],
        picker=spec,
        options={"until": "2026-12-20"} if key == "revision_plan" else None,
        now=T0,
    )
    assert doc.left_out_count == 1 and doc.recipe_key == key

    answer = [
        para("Both met.", [source("meeting", m0), source("meeting", m1)]),
        para("Decided.", [source("decision", m0, d0.id)]),
        para("To do.", [source("task", m1, t1.id)]),
    ]
    model = FakeModel(reply(answer))
    svc.process_next(session, T0, client=model)
    system, user = system_and_user(model.calls[0])

    # the starter's own instruction leads, the material is notes, decisions, tasks and questions
    assert library.starter_by_key(key).instruction in user
    assert user.count("<meeting>") == 2
    for n, (_, decision, task) in enumerate(two):
        assert f"Notes of meeting {n}." in user
        assert f"[D{decision.id}] Decision {n}" in user
        assert f"[T{task.id}] Task {n}" in user
        assert f"Question {n}?" in user
    # no transcript, no line numbers, nothing of the meeting the caller cannot read
    assert SECRET not in user and "[L1]" not in user and "Private chat" not in user
    assert "Meeting id:" in user

    session.refresh(doc)
    assert doc.status == "ready"
    stored = blocks_mod.load_blocks(doc.blocks)
    sources = [s for block in stored for s in block.sources]
    assert {s.kind for s in sources} == {"meeting", "decision", "task"}
    assert all(s.meeting_id in {m0.id, m1.id} for s in sources)
    assert not any(s.kind == "line" for s in sources)


def test_the_revision_plan_option_reaches_the_model_as_data(session, daniel, two):
    (m0, _, _), (m1, _, _) = two
    svc.request_draft(
        session,
        daniel,
        recipe_ref="revision_plan",
        meeting_ids=[m0.id, m1.id],
        options={"until": "2026-12-20"},
        now=T0,
    )
    model = FakeModel(reply([para("Plan.", [source("meeting", m0)])]))
    svc.process_next(session, T0, client=model)
    user = system_and_user(model.calls[0])[1]
    assert '"until": "2026-12-20"' in user
    assert session.query(Document).one().status == "ready"
