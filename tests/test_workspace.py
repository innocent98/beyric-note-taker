import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import ai_runs, workspace
from app.models import AiRun, Decision, Meeting, OpenQuestion, ReviewItem, Template, User

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
TRANSCRIPT = "[Adebayo]: We use Metrika now.\n[Grace]: Launch is fourteenth of March."
NOTES = """\
## Summary
Short.

## Key points
- A point.

## Decisions
- Launch moves to 14 March.
- Budget is capped at £4,000.

## Action items
- Tunde: send the contract (by Friday)

## Open questions
- Which email provider do we pick?

## Please check
- "Metrika" may be misheard.
- Grace's budget figure is unclear.
"""


class FakeChat:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if not isinstance(item, str):
            item = json.dumps(item)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=700, completion_tokens=90),
        )


GOOD = {"action_items": [], "review": [{"index": 0, "heard_as": "Metrika"}], "milestones": []}


@pytest.fixture
def user(session):
    row = User(email="owner@example.test")
    session.add(row)
    session.flush()
    session.add(Template(owner_user_id=row.id, name="General", instructions="x"))
    session.commit()
    return row


def make_meeting(session, user, *, notes=NOTES, status="ready", created=NOW, **fields):
    template = session.scalars(select(Template)).first()
    meeting = Meeting(
        owner_user_id=user.id,
        url="u",
        platform="zoom",
        native_id="1",
        template_id=template.id,
        status=status,
        step="done" if status == "ready" else "waiting",
        notes=notes,
        speaker_transcript=TRANSCRIPT,
        created_at=created,
        **fields,
    )
    session.add(meeting)
    session.commit()
    return meeting


@pytest.fixture
def meeting(session, user):
    return make_meeting(session, user)


def rows(session, model):
    return list(session.scalars(select(model).order_by(model.position)))


def test_a_run_stores_what_the_notes_say_and_what_the_model_heard(session, meeting):
    client = FakeChat([GOOD])
    status = workspace.run_extraction(session, meeting, client=client, now=NOW)
    assert status == "done"
    assert [d.text for d in rows(session, Decision)] == [
        "Launch moves to 14 March.",
        "Budget is capped at £4,000.",
    ]
    assert [q.text for q in rows(session, OpenQuestion)] == ["Which email provider do we pick?"]
    review = rows(session, ReviewItem)
    assert [(r.text, r.heard_as, r.owner_user_id) for r in review] == [
        ('"Metrika" may be misheard.', "Metrika", meeting.owner_user_id),
        ("Grace's budget figure is unclear.", None, meeting.owner_user_id),
    ]
    session.refresh(meeting)
    assert (meeting.extraction_status, meeting.extraction_version) == (
        "done",
        ai_runs.EXTRACT_PROMPT_VERSION,
    )
    run = session.scalars(select(AiRun)).one()
    assert (run.kind, run.status, run.input_tokens, run.output_tokens) == ("extract", "ok", 700, 90)
    assert run.meeting_id == meeting.id


def test_the_call_is_only_asked_about_the_please_check_bullets_in_w1(session, meeting):
    client = FakeChat([GOOD])
    workspace.run_extraction(session, meeting, client=client, now=NOW)
    prompt = "\n".join(m["content"] for m in client.calls[0]["messages"])
    assert '0: "Metrika" may be misheard.' in prompt
    assert "send the contract" not in prompt.split("<transcript>")[0]  # no action items asked


def test_a_term_the_transcript_never_had_is_not_kept(session, meeting):
    reply = {"action_items": [], "review": [{"index": 0, "heard_as": "Zorblax"}], "milestones": []}
    workspace.run_extraction(session, meeting, client=FakeChat([reply]), now=NOW)
    assert [r.heard_as for r in rows(session, ReviewItem)] == [None, None]


def test_no_call_is_made_when_there_is_nothing_to_check(session, user):
    notes = NOTES.replace(
        '- "Metrika" may be misheard.\n- Grace\'s budget figure is unclear.', "None noted"
    )
    meeting = make_meeting(session, user, notes=notes)
    client = FakeChat([])
    assert workspace.run_extraction(session, meeting, client=client, now=NOW) == "done"
    assert client.calls == [] and session.scalars(select(AiRun)).all() == []
    assert len(rows(session, Decision)) == 2  # the code-read lists are there regardless


def test_a_failed_call_keeps_the_lists_and_retries_up_to_three_times(session, meeting):
    for attempt, expected in ((1, "pending"), (2, "pending"), (3, "failed")):
        status = workspace.run_extraction(
            session, meeting, client=FakeChat(["not json"]), now=NOW + timedelta(hours=attempt)
        )
        assert status == expected
    session.refresh(meeting)
    assert (meeting.extraction_status, meeting.extraction_attempts) == ("failed", 3)
    assert len(rows(session, Decision)) == 2 and len(rows(session, ReviewItem)) == 2
    runs = session.scalars(select(AiRun)).all()
    assert [r.status for r in runs] == ["failed"] * 3
    assert {r.error for r in runs} == {"ExtractionFailed"}
    assert all(r.input_tokens == 700 for r in runs)


def test_an_unexpected_error_in_the_call_is_recorded_by_class_name_only(session, meeting):
    status = workspace.run_extraction(
        session, meeting, client=FakeChat([RuntimeError("secret words")]), now=NOW
    )
    assert status == "pending"
    assert session.scalars(select(AiRun)).one().error == "RuntimeError"


def test_a_later_success_after_a_failure_fills_heard_as(session, meeting):
    workspace.run_extraction(session, meeting, client=FakeChat(["bad"]), now=NOW)
    assert [r.heard_as for r in rows(session, ReviewItem)] == [None, None]
    workspace.run_extraction(session, meeting, client=FakeChat([GOOD]), now=NOW)
    assert [r.heard_as for r in rows(session, ReviewItem)] == ["Metrika", None]


def test_a_rerun_keeps_what_a_person_did_and_follows_the_new_notes(session, meeting):
    workspace.run_extraction(session, meeting, client=FakeChat([GOOD]), now=NOW)
    first = rows(session, Decision)[0]
    first.hidden_at = NOW
    checked = rows(session, ReviewItem)[1]
    checked.checked_at = NOW
    kept_ids = (first.id, checked.id)
    session.commit()

    meeting.notes = (
        NOTES.replace(
            "- Budget is capped at £4,000.", "- Hiring is paused.\n- Budget is capped at £4,000."
        )
        .replace("- Launch moves to 14 March.\n", "- Launch moves to 14 March.\n")
        .replace("- Which email provider do we pick?", "- Who owns VAT?")
    )
    session.commit()
    workspace.run_extraction(session, meeting, client=FakeChat([GOOD]), now=NOW)

    decisions = rows(session, Decision)
    assert [d.text for d in decisions] == [
        "Launch moves to 14 March.",
        "Hiring is paused.",
        "Budget is capped at £4,000.",
    ]
    assert decisions[0].id == kept_ids[0] and decisions[0].hidden_at is not None
    assert [q.text for q in rows(session, OpenQuestion)] == ["Who owns VAT?"]
    review = rows(session, ReviewItem)
    assert review[1].id == kept_ids[1] and review[1].checked_at is not None


def test_old_meetings_get_decisions_only_and_the_model_is_not_asked(session, meeting):
    client = FakeChat([])
    status = workspace.run_extraction(session, meeting, client=client, now=NOW, with_ai=False)
    assert status == "skipped" and client.calls == []
    assert len(rows(session, Decision)) == 2
    assert rows(session, OpenQuestion) == [] and rows(session, ReviewItem) == []


def test_a_meeting_that_is_not_ready_is_left_alone(session, user):
    meeting = make_meeting(session, user, status="processing", notes=None)
    assert workspace.run_extraction(session, meeting, client=FakeChat([]), now=NOW) is None
    session.refresh(meeting)
    assert meeting.extraction_status is None


def test_deleting_the_meeting_deletes_what_was_read_from_it_and_keeps_the_ledger(session, meeting):
    workspace.run_extraction(session, meeting, client=FakeChat([GOOD]), now=NOW)
    session.delete(meeting)
    session.commit()
    for model in (Decision, OpenQuestion, ReviewItem):
        assert session.scalars(select(model)).all() == []
    assert session.scalars(select(AiRun)).one().meeting_id is None


def test_mark_pending_starts_the_states_over(session, meeting):
    meeting.extraction_status, meeting.extraction_attempts = "failed", 3
    meeting.extraction_attempted_at = NOW
    workspace.mark_pending(meeting)
    assert (meeting.extraction_status, meeting.extraction_attempts) == ("pending", 0)
    assert meeting.extraction_attempted_at is None


# ---- which meeting the worker takes next ----


def pending(session, user, **fields):
    meeting = make_meeting(session, user, extraction_status="pending", **fields)
    return meeting


def test_the_worker_takes_a_pending_meeting_and_runs_it(session, user):
    meeting = pending(session, user)
    assert workspace.process_next(session, NOW, client=FakeChat([GOOD])) == meeting.id
    session.refresh(meeting)
    assert meeting.extraction_status == "done"
    assert workspace.process_next(session, NOW, client=FakeChat([])) is None


def test_a_retry_waits_for_the_gap_and_a_stuck_run_is_taken_back(session, user):
    waiting = pending(session, user, extraction_attempts=1, extraction_attempted_at=NOW)
    assert workspace.claim_next(session, NOW + timedelta(minutes=1)) is None
    assert workspace.claim_next(session, NOW + workspace.RETRY_GAP).id == waiting.id
    session.rollback()

    stuck = make_meeting(
        session,
        user,
        extraction_status="running",
        extraction_attempts=1,
        extraction_attempted_at=NOW,
    )
    waiting.extraction_status = "done"
    session.commit()
    assert workspace.claim_next(session, NOW + timedelta(minutes=5)) is None
    assert workspace.claim_next(session, NOW + workspace.STALE_RUNNING).id == stuck.id


def test_a_meeting_out_of_attempts_is_failed_not_retried(session, user):
    spent = pending(session, user, extraction_attempts=workspace.MAX_ATTEMPTS)
    assert workspace.claim_next(session, NOW) is None
    session.refresh(spent)
    assert spent.extraction_status == "failed"


def test_other_states_and_deleted_owners_are_not_taken(session, user):
    make_meeting(session, user, extraction_status="done")
    make_meeting(session, user, extraction_status="skipped")
    make_meeting(session, user, extraction_status=None)
    ghost = User(email="gone@example.test", status="deleted")
    session.add(ghost)
    session.commit()
    pending(session, ghost)
    assert workspace.claim_next(session, NOW) is None


# ---- backfill ----


def test_backfill_gives_recent_meetings_the_full_run_and_old_ones_decisions_only(session, user):
    recent = make_meeting(session, user, created=NOW - timedelta(days=3))
    old = make_meeting(session, user, created=NOW - timedelta(days=40))
    done = make_meeting(session, user, extraction_status="done")
    not_ready = make_meeting(session, user, status="processing", notes=None)

    counts = workspace.backfill(session, NOW)

    assert counts == {"queued": 1, "decisions_only": 1}
    for row in (recent, old, done, not_ready):
        session.refresh(row)
    assert recent.extraction_status == "pending"
    assert old.extraction_status == "skipped"
    assert done.extraction_status == "done" and not_ready.extraction_status is None
    old_decisions = session.scalars(select(Decision).where(Decision.meeting_id == old.id)).all()
    assert len(old_decisions) == 2
    assert session.scalars(select(ReviewItem).where(ReviewItem.meeting_id == old.id)).all() == []
    assert workspace.backfill(session, NOW) == {"queued": 0, "decisions_only": 0}  # idempotent


# ---- the worker runs it ----


def test_the_worker_job_extracts_and_a_failing_job_cannot_stop_the_others(
    session, user, monkeypatch, caplog
):
    from app import extraction, worker

    meeting = pending(session, user)
    monkeypatch.setattr(extraction, "OpenAI", lambda **kw: FakeChat([GOOD]))
    worker.background_jobs(session, None, NOW)
    session.refresh(meeting)
    assert meeting.extraction_status == "done"

    def boom(*args, **kwargs):
        raise ValueError("secret transcript words")

    monkeypatch.setattr(worker.workspace, "process_next", boom)
    caplog.set_level("WARNING")
    worker.background_jobs(session, None, NOW)  # must not raise
    ours = " ".join(r.getMessage() for r in caplog.records)
    assert "extraction job failed (ValueError)" in ours and "secret" not in ours
