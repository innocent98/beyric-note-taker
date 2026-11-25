# ruff: noqa: F811
"""What a cross-meeting draft is made from and what it can cite (plan T15, spec 5.3b): per meeting
the notes, decisions, tasks and open questions, never several full transcripts, and sources of the
kinds `meeting`, `decision` and `task` that name their meeting. A stand-in recipe plays the status
report, the client briefing and the revision plan (their real instructions are in
`app/recipes/library.py`)."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import teams
from app.models import Decision, Document, OpenQuestion, Task, TeamMember
from app.recipes import generate as g
from app.recipes import picker
from app.recipes import service as svc
from app.recipes.picker_spec import PickerSpec
from tests.test_api import ready  # noqa: F401
from tests.test_recipe_generate import FakeModel, para, reply, system_and_user
from tests.test_web import make_user, owner, template  # noqa: F401  (fixtures used by name)

T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
SECRET = "SECRET-TRANSCRIPT-WORDS"
STAND_IN = SimpleNamespace(
    key="status_report",
    name="Status report",
    instruction="Progress, decisions, blockers and next steps over the meetings.",
    output_type="document",
    scope="many",
    uses_brief=False,
)


@pytest.fixture(autouse=True)
def starters(monkeypatch):
    monkeypatch.setattr(svc, "starter_for", {"status_report": STAND_IN}.get)


@pytest.fixture
def team(session, owner):
    return teams.ensure_team(session, owner)


@pytest.fixture
def daniel(session, team):
    user = make_user(session, "daniel@example.test", name="Daniel Okoro")
    session.add(TeamMember(team_id=team.id, user_id=user.id, role="member"))
    session.commit()
    return user


def held(session, template, team, n, *, shared=True, **fields):
    values = {
        "title": f"Meeting {n}",
        "created_at": T0 + timedelta(days=n),
        "speaker_transcript": f"[Zed]: {SECRET} number {n} about budgets.",
        "transcript": f"[Zed]: {SECRET} number {n} about budgets.",
        "notes": f"## Summary\nNotes of meeting {n}.",
    }
    found = ready(session, template, **{**values, **fields})
    if shared:
        found.shared_team_id = team.id
        session.commit()
    return found


@pytest.fixture
def two(session, owner, daniel, template, team):
    """Two shared meetings with a decision, a task and an open question each."""
    first = held(session, template, team, 0)
    second = held(session, template, team, 1)
    out = []
    for n, meeting in enumerate((first, second)):
        decision = Decision(
            meeting_id=meeting.id, text=f"Decision {n}", position=0, fingerprint=f"d{n}"
        )
        task = Task(
            team_id=team.id,
            meeting_id=meeting.id,
            title=f"Task {n}",
            spoken_owner="Ada",
            due_date=date(2026, 12, 1),
            source="ai",
            ai_fingerprint=f"t{n}",
        )
        session.add_all(
            [
                decision,
                task,
                OpenQuestion(
                    meeting_id=meeting.id, text=f"Question {n}?", position=0, fingerprint=f"q{n}"
                ),
            ]
        )
        session.flush()
        out.append((meeting, decision, task))
    session.commit()
    return out


def source(kind, meeting, ref=None):
    return {"kind": kind, "meeting_id": meeting.id, "line": None, "quote": None, "ref": ref}


def chosen(session, user, ids):
    return picker.resolve_meetings(session, user, PickerSpec(meeting_ids=ids)).meetings


def test_the_prompt_has_each_meetings_notes_decisions_tasks_and_questions_and_no_transcript(
    session, daniel, two
):
    meetings = chosen(session, daniel, [m.id for m, _, _ in two])
    context = g.build_context(session, daniel, meetings, whole_transcript=False, brief=None)
    model = FakeModel(reply([para("Steady progress.")]))
    g.generate_blocks(g.spec_of(STAND_IN), context, None, client=model)
    _, user_message = system_and_user(model.calls[0])
    assert SECRET not in user_message and "[L1]" not in user_message
    assert user_message.count("<meeting>") == 2
    for n, (meeting, decision, task) in enumerate(two):
        assert f"Meeting id: {meeting.id}" in user_message
        assert f"Notes of meeting {n}." in user_message
        assert f"[D{decision.id}] Decision {n}" in user_message
        assert f"[T{task.id}] Task {n}" in user_message
        assert f"Question {n}?" in user_message


def test_sources_come_back_as_meeting_decision_and_task_and_name_their_meeting(
    session, daniel, two
):
    (m0, d0, t0), (m1, d1, t1) = two
    meetings = chosen(session, daniel, [m0.id, m1.id])
    context = g.build_context(session, daniel, meetings, whole_transcript=False, brief=None)
    reply_blocks = [
        para("Both meetings met their goals.", [source("meeting", m0), source("meeting", m1)]),
        para("Decided: decision 0.", [source("decision", m0, d0.id)]),
        para("Next: task 1.", [source("task", m1, t1.id)]),
    ]
    result = g.generate_blocks(
        g.spec_of(STAND_IN), context, None, client=FakeModel(reply(reply_blocks))
    )
    got = [[(s.kind, s.meeting_id, s.ref) for s in b.sources] for b in result.blocks]
    assert got == [
        [("meeting", m0.id, None), ("meeting", m1.id, None)],
        [("decision", m0.id, d0.id)],
        [("task", m1.id, t1.id)],
    ]


def test_a_line_source_or_another_meetings_decision_is_dropped(session, daniel, two):
    (m0, d0, _), (m1, _, _) = two
    context = g.build_context(
        session, daniel, chosen(session, daniel, [m0.id, m1.id]), whole_transcript=False, brief=None
    )
    line = {"kind": "line", "meeting_id": m0.id, "line": 1, "quote": None, "ref": None}
    blocks = [para("x", [line, source("decision", m1, d0.id), source("meeting", m0)])]
    result = g.generate_blocks(g.spec_of(STAND_IN), context, None, client=FakeModel(reply(blocks)))
    assert [(s.kind, s.meeting_id) for s in result.blocks[0].sources] == [("meeting", m0.id)]


def test_a_cross_meeting_draft_never_sends_a_full_transcript_through_the_worker(
    session, daniel, two
):
    (m0, _, _), (m1, _, t1) = two
    doc = svc.request_draft(
        session, daniel, recipe_ref="status_report", meeting_ids=[m0.id, m1.id], now=T0
    )
    model = FakeModel(reply([para("Next: task 1.", [source("task", m1, t1.id)])]))
    svc.process_next(session, T0, client=model)
    session.refresh(doc)
    assert doc.status == "ready"
    assert SECRET not in system_and_user(model.calls[0])[1]
    assert doc.blocks[0]["sources"][0]["kind"] == "task"
    assert doc.blocks[0]["sources"][0]["meeting_id"] == m1.id


def test_one_meeting_left_after_the_others_are_unreadable_is_still_not_a_full_transcript(
    session, owner, daniel, template, team, two
):
    (m0, _, _), _ = two
    hidden = held(session, template, team, 5, shared=False, title="Private chat")
    doc = svc.request_draft(
        session,
        daniel,
        recipe_ref="status_report",
        meeting_ids=[m0.id, hidden.id],
        picker=PickerSpec(meeting_ids=[m0.id, hidden.id]),
        now=T0,
    )
    assert doc.left_out_count == 1
    model = FakeModel(reply([para("Fine.", [source("meeting", m0)])]))
    svc.process_next(session, T0, client=model)
    user_message = system_and_user(model.calls[0])[1]
    assert SECRET not in user_message and "[L1]" not in user_message
    assert "Private chat" not in user_message
    assert session.get(Document, doc.id).left_out_count == 1
    assert session.scalar(select(Document.status).where(Document.id == doc.id)) == "ready"
