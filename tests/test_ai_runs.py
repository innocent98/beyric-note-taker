import pytest
from sqlalchemy.exc import IntegrityError

from app import ai_runs
from app.config import Settings
from app.models import AiRun, Meeting, Template, User


def test_extract_model_defaults_to_the_notes_model_and_is_settable(monkeypatch):
    monkeypatch.delenv("EXTRACT_MODEL", raising=False)
    assert Settings(_env_file=None).extract_model == "gpt-6-luna"
    monkeypatch.setenv("EXTRACT_MODEL", "some-stronger-model")
    assert Settings(_env_file=None).extract_model == "some-stronger-model"


def test_prompt_versions_are_named_and_distinct():
    versions = [
        ai_runs.NOTES_PROMPT_VERSION,
        ai_runs.LABEL_PROMPT_VERSION,
        ai_runs.EXTRACT_PROMPT_VERSION,
        ai_runs.EXTRACT_REVIEW_PROMPT_VERSION,
    ]
    assert all(v and v.strip() == v for v in versions)
    assert len(set(versions)) == 4


@pytest.fixture
def meeting(session):
    user = User(email="a@example.test")
    session.add(user)
    session.flush()
    template = Template(owner_user_id=user.id, name="General", instructions="x")
    session.add(template)
    session.flush()
    row = Meeting(
        owner_user_id=user.id, url="u", platform="zoom", native_id="1", template_id=template.id
    )
    session.add(row)
    session.commit()
    return row


def test_record_stores_tokens_and_the_prompt_version(session, meeting):
    run = ai_runs.record(
        session, meeting.id, "extract", "gpt-6-luna", input_tokens=900, output_tokens=120
    )
    session.commit()
    saved = session.get(AiRun, run.id)
    assert (saved.kind, saved.status, saved.error) == ("extract", "ok", None)
    assert (saved.input_tokens, saved.output_tokens) == (900, 120)
    assert saved.prompt_version == ai_runs.EXTRACT_PROMPT_VERSION
    assert saved.created_at is not None


def test_a_run_can_name_its_own_prompt_version(session, meeting):
    run = ai_runs.record(
        session, meeting.id, "extract", "m", prompt_version=ai_runs.EXTRACT_REVIEW_PROMPT_VERSION
    )
    assert run.prompt_version == ai_runs.EXTRACT_REVIEW_PROMPT_VERSION


def test_a_failed_run_keeps_the_class_name_only(session, meeting):
    run = ai_runs.record(
        session, meeting.id, "extract", "m", status="failed", error=ValueError("secret text")
    )
    session.commit()
    assert (run.status, run.error) == ("failed", "ValueError")


def test_a_wrapped_error_is_recorded_as_its_cause(session, meeting):
    try:
        try:
            raise TimeoutError("secret words")
        except TimeoutError as cause:
            raise ValueError("wrapper") from cause
    except ValueError as wrapped:
        run = ai_runs.record(session, meeting.id, "extract", "m", status="failed", error=wrapped)
    assert run.error == "TimeoutError"


def test_the_ledger_outlives_the_meeting(session, meeting):
    ai_runs.record(session, meeting.id, "extract", "m", input_tokens=1, output_tokens=1)
    session.commit()
    session.delete(meeting)
    session.commit()
    row = session.query(AiRun).one()
    assert row.meeting_id is None


def test_kind_and_status_are_checked(session, meeting):
    session.add(AiRun(meeting_id=meeting.id, kind="bogus", model="m", status="ok"))
    with pytest.raises(IntegrityError):
        session.commit()
