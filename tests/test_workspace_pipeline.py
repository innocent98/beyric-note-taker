"""The pipeline, the notes rewrite, the translation and the mails read the MEETING's workspace
(workspaces design, sections 7 and 12.7; plan Task 6).

A meeting filed in team workspace X is written with X's vocabulary and X's template, never with the
personal lists of whoever recorded it, and all of it keeps working when the recorder has left: the
workspace outlives them. A team admin rewriting a moved meeting chooses among the WORKSPACE's
templates (the old rule was the recorder's, so an admin could never choose, and a recorder who had
left left nothing to choose from). Mail is sent only when the person's own preference allows it AND,
for a workspace-level mail such as a reminder, the workspace setting is on.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import pytest
from sqlalchemy import delete

from app import accounts, services, teams, translation, workspace_prefs
from app.models import (
    Meeting,
    MeetingTranslation,
    Team,
    TeamMember,
    Template,
    VocabularyTerm,
    WorkspacePrefs,
)
from tests.test_api import error_of  # noqa: F401
from tests.test_mail_summary import JOIN, box, live_join, mail_of
from tests.test_pipeline import DONE, NOW, FakeBot, FakeOpenAI, env, run  # noqa: F401
from tests.test_series import daniel, owner, template, weekly  # noqa: F401
from tests.test_series_mail import OWNER_EMAIL, busy, due  # noqa: F401
from tests.test_web import make_user

BOARD = "BOARD_INSTRUCTIONS: list the votes."
MINE = "PERSONAL_INSTRUCTIONS: be brief."


class RecordingOpenAI(FakeOpenAI):
    """Keeps every prompt the model was sent."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.prompts: list[str] = []

    def _chat(self, **kwargs):
        self.prompts.append("\n".join(m["content"] for m in kwargs["messages"]))
        return super()._chat(**kwargs)

    def _transcribe(self, **kwargs):
        self.prompts.append(str(kwargs.get("prompt", "")))
        return super()._transcribe(**kwargs)


class Cast:
    pass


@pytest.fixture
def cast(session, owner) -> Cast:
    """Pat records into team X, whose owner is Ada. Pat's personal lists differ on purpose."""
    c = Cast()
    c.ada = owner
    c.pat = make_user(session, "pat@example.test")
    c.quinn = make_user(session, "quinn@example.test")
    for user in (c.ada, c.pat, c.quinn):
        accounts.create_user_defaults(session, user)
    c.x = teams.create_owned_team(session, c.ada, "X")
    session.add_all(
        [
            TeamMember(team_id=c.x.id, user_id=c.pat.id, role="member"),
            TeamMember(team_id=c.x.id, user_id=c.quinn.id, role="admin"),
        ]
    )
    c.personal = teams.personal_team(session, c.pat.id)
    c.board = Template(owner_user_id=c.ada.id, team_id=c.x.id, name="Board", instructions=BOARD)
    c.mine = Template(owner_user_id=c.pat.id, team_id=c.personal.id, name="Mine", instructions=MINE)
    session.add_all(
        [
            c.board,
            c.mine,
            VocabularyTerm(owner_user_id=c.ada.id, team_id=c.x.id, term="LURAN_TERM"),
            VocabularyTerm(owner_user_id=c.pat.id, team_id=c.personal.id, term="PERSONAL_TERM"),
        ]
    )
    session.commit()
    return c


def meeting_in_x(session, cast, **fields) -> Meeting:
    values = {
        "owner_user_id": cast.pat.id,
        "url": "https://meet.google.com/abc-defg-hij",
        "platform": "google_meet",
        "native_id": "abc-defg-hij",
        "template_id": cast.board.id,
        "workspace_team_id": cast.x.id,
        "started_polling_at": NOW,
    }
    meeting = Meeting(**{**values, **fields})
    session.add(meeting)
    session.commit()
    return meeting


# ---- the pipeline ------------------------------------------------------------------------------


def test_a_members_meeting_uses_the_workspaces_vocabulary_and_template_not_their_own(
    session, cast, env
):
    meeting = meeting_in_x(session, cast)
    openai = RecordingOpenAI()
    run(session, meeting, FakeBot(DONE), openai)
    assert meeting.status == "ready"
    sent = "\n".join(openai.prompts)
    assert "LURAN_TERM" in sent and BOARD in sent
    assert "PERSONAL_TERM" not in sent and MINE not in sent


def test_it_still_works_after_the_recorder_left_the_team(session, cast, env):
    """A meeting MOVED to X outlives its recorder's membership: the vocabulary is X's."""
    meeting = meeting_in_x(session, cast, owner_team_id=cast.x.id)
    session.execute(
        delete(TeamMember).where(TeamMember.team_id == cast.x.id, TeamMember.user_id == cast.pat.id)
    )
    session.commit()
    openai = RecordingOpenAI()
    run(session, meeting, FakeBot(DONE), openai)
    assert meeting.status == "ready"
    assert "LURAN_TERM" in "\n".join(openai.prompts)
    assert "PERSONAL_TERM" not in "\n".join(openai.prompts)


def test_the_workspace_context_names_the_vocabulary_and_default_template(session, cast):
    meeting = meeting_in_x(session, cast)
    context = workspace_prefs.for_meeting(session, meeting)
    assert context.team_id == cast.x.id
    assert context.terms == ["LURAN_TERM"]
    default = session.get(Template, context.default_template_id)
    assert default.team_id == cast.x.id  # one of X's, never Pat's personal "Mine"
    assert context.recorder is not None and context.recorder.team_id == cast.x.id


def test_the_context_needs_no_recorder_account(session, cast):
    meeting = meeting_in_x(session, cast)
    cast.pat.status = "deleted"
    session.commit()
    context = workspace_prefs.for_meeting(session, meeting)
    assert context.terms == ["LURAN_TERM"]
    assert context.default_template_id is not None


# ---- the notes rewrite ---------------------------------------------------------------------


def ready_in_x(session, cast, **fields) -> Meeting:
    return meeting_in_x(
        session,
        cast,
        status="ready",
        step="done",
        transcript="We decided LURAN_TERM pricing.",
        speaker_transcript="[Pat]: We decided LURAN_TERM pricing.",
        notes="## Summary\nOld.\n",
        **fields,
    )


def test_a_team_admin_rewriting_a_moved_meeting_chooses_among_the_workspaces_templates(
    session, cast
):
    meeting = ready_in_x(session, cast, owner_team_id=cast.x.id)
    openai = RecordingOpenAI()
    done = services.regenerate_notes(session, cast.quinn, meeting, cast.board.id, openai)
    assert done.template_id == cast.board.id
    sent = "\n".join(openai.prompts)
    assert BOARD in sent and "LURAN_TERM" in sent and "PERSONAL_TERM" not in sent


def test_a_personal_template_of_the_recorder_is_not_a_choice_in_the_workspace(session, cast):
    meeting = ready_in_x(session, cast)
    with pytest.raises(services.Refused) as refused:
        services.regenerate_notes(session, cast.pat, meeting, cast.mine.id, RecordingOpenAI())
    assert refused.value.code == "template_required"


def test_the_rewrite_works_when_the_recorder_has_left(session, cast):
    meeting = ready_in_x(session, cast, owner_team_id=cast.x.id)
    session.execute(
        delete(TeamMember).where(TeamMember.team_id == cast.x.id, TeamMember.user_id == cast.pat.id)
    )
    session.commit()
    done = services.regenerate_notes(session, cast.quinn, meeting, cast.board.id, RecordingOpenAI())
    assert done.template_id == cast.board.id


# ---- the translation -----------------------------------------------------------------------


def test_a_translation_is_given_the_workspaces_terms(session, cast):
    meeting = ready_in_x(session, cast)
    row = MeetingTranslation(
        meeting_id=meeting.id,
        language="fr",
        status="translating",
        source_hash="x",
        model="m",
    )
    session.add(row)
    session.commit()
    job = translation._read_job(session, row, NOW)
    assert job is not None and job.terms == ["LURAN_TERM"]


# ---- mail ---------------------------------------------------------------------------------


def test_a_reminder_needs_the_persons_preference_and_the_workspace_setting():
    wants = True
    on, off = Team(reminders_on=True), Team(reminders_on=False)
    assert workspace_prefs.mail_allowed(wants, on, workspace_level=True) is True
    assert workspace_prefs.mail_allowed(wants, off, workspace_level=True) is False
    assert workspace_prefs.mail_allowed(False, on, workspace_level=True) is False
    # a mail about the person's own meeting has no workspace switch
    assert workspace_prefs.mail_allowed(wants, off, workspace_level=False) is True
    assert workspace_prefs.mail_allowed(False, off, workspace_level=False) is False


def test_the_join_sentence_in_a_reminder_reads_the_rules_of_the_series_workspace(
    session, owner, daniel, weekly, busy, due
):
    live_join(session, owner, due)
    assert box(mail_of(session, OWNER_EMAIL)) == JOIN
    # their rule in ANOTHER workspace changes nothing here
    other = teams.create_owned_team(session, owner, "Elsewhere")
    session.add(WorkspacePrefs(team_id=other.id, user_id=owner.id, auto_join_rule="none"))
    session.commit()
    assert box(mail_of(session, OWNER_EMAIL)) == JOIN
    # their rule in the series' own workspace decides
    session.add(WorkspacePrefs(team_id=weekly.team_id, user_id=owner.id, auto_join_rule="none"))
    session.commit()
    assert box(mail_of(session, OWNER_EMAIL)) is None
