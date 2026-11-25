"""A workspace id taken from a meeting or a connection is never used for a person who is no
longer in that workspace (defence in depth after the W2 review).

* The notes rewrite and the translation read the MEETING's workspace (its templates and vocabulary):
  only a CURRENT member of it may ask. A person who left, or was never in it, is refused with the
  same 404 an unreadable meeting gets, and nothing of the workspace is read or used. (Safest of the
  two choices: a fall-back to their personal templates would still run a model call on a meeting
  whose workspace they no longer belong to.)
* The calendar screen reads and writes the person's settings in their CONNECTION's workspace: for a
  person who left it, it is their default workspace instead, and the old workspace's template names
  and ids are never shown or accepted.
* A calendar join filed in a workspace the person left records into their personal workspace.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app import services, teams, translation, workspace_prefs
from app.models import Meeting, TeamMember, Template, WorkspacePrefs
from app.placement import PlacementRequest, place_bot
from tests.test_api import error_of  # noqa: F401
from tests.test_calendar_settings_api import (  # noqa: F401  (fixtures used by name)
    SETTINGS_URL,
    URL,
    capp,
    fake_bot,
    fake_openai,
    google,
    linked,
    make_template,
    owner,
    settings,
    stranger,
    vault_key,
    web,
)
from tests.test_pipeline import NOW  # noqa: F401
from tests.test_web import make_settings, make_user
from tests.test_workspace_pipeline import (  # noqa: F401
    RecordingOpenAI,
    cast,
    ready_in_x,
)
from tests.workspace_plans import subscribe


def leave(session, cast):
    session.execute(
        delete(TeamMember).where(TeamMember.team_id == cast.x.id, TeamMember.user_id == cast.pat.id)
    )
    session.commit()


# ---- the notes rewrite ----------------------------------------------------------------------


def test_a_current_member_who_recorded_the_meeting_rewrites_with_the_workspaces_template(
    session, cast
):
    meeting = ready_in_x(session, cast)
    done = services.regenerate_notes(session, cast.pat, meeting, cast.board.id, RecordingOpenAI())
    assert done.template_id == cast.board.id


def test_a_former_member_cannot_rewrite_and_nothing_of_the_workspace_is_read(session, cast):
    meeting = ready_in_x(session, cast)
    before, before_template = meeting.notes, meeting.template_id
    leave(session, cast)
    openai = RecordingOpenAI()
    for template_id in (cast.board.id, cast.mine.id):  # the workspace's, and their own personal one
        with pytest.raises(HTTPException) as refused:
            services.regenerate_notes(session, cast.pat, meeting, template_id, openai)
        assert refused.value.status_code == 404
    assert openai.prompts == []  # no model call: the vocabulary was never read for them
    session.refresh(meeting)
    assert meeting.notes == before and meeting.template_id == before_template


def test_a_person_who_was_never_in_the_workspace_cannot_rewrite(session, cast):
    meeting = ready_in_x(session, cast)
    stranger_user = make_user(session, "zed@example.test")
    openai = RecordingOpenAI()
    with pytest.raises(HTTPException) as refused:
        services.regenerate_notes(session, stranger_user, meeting, cast.board.id, openai)
    assert refused.value.status_code == 404 and openai.prompts == []


def test_an_admin_of_a_moved_meeting_still_rewrites_it(session, cast):
    meeting = ready_in_x(session, cast, owner_team_id=cast.x.id)
    leave(session, cast)  # the recorder left; the team's admin is a current member
    done = services.regenerate_notes(session, cast.quinn, meeting, cast.board.id, RecordingOpenAI())
    assert done.template_id == cast.board.id


# ---- the translation ------------------------------------------------------------------------


def test_a_former_member_cannot_queue_a_translation_of_a_meeting_of_the_workspace(session, cast):
    meeting = ready_in_x(session, cast)
    leave(session, cast)
    with pytest.raises(HTTPException) as refused:
        translation.request_translation(session, cast.pat, meeting, "fr")
    assert refused.value.status_code == 404


# ---- the calendar screen --------------------------------------------------------------------


@pytest.fixture
def old_workspace(session, owner, linked):
    """The owner's calendar is connected in team X (with a template of its own), and the owner is
    a member of it."""
    other = make_user(session, "boss@example.test")
    subscribe(session, other, "plan_20h")  # owning a team needs a plan
    x = teams.create_owned_team(session, other, "Old workspace")
    session.add(TeamMember(team_id=x.id, user_id=owner.id, role="member"))
    secret = Template(
        owner_user_id=other.id, team_id=x.id, name="OLD_WORKSPACE_SECRET", instructions="x"
    )
    session.add(secret)
    linked.team_id = x.id
    session.commit()
    return x, secret


def test_a_current_member_reads_and_writes_the_connections_workspace(
    web, session, owner, old_workspace
):
    x, secret = old_workspace
    done = web.put(SETTINGS_URL, {"rule": "none", "calendar_template_id": secret.id})
    assert done.status_code == 200, done.text
    session.expire_all()
    row = session.get(WorkspacePrefs, (x.id, owner.id))
    assert row.auto_join_rule == "none" and row.calendar_template_id == secret.id
    assert web.get(URL).json()["rule"] == "none"


def test_a_leaver_cannot_read_or_write_the_old_workspace_through_the_old_connection(
    web, session, owner, old_workspace
):
    x, secret = old_workspace
    session.add(WorkspacePrefs(team_id=x.id, user_id=owner.id, auto_join_rule="organizer"))
    session.commit()
    session.execute(
        delete(TeamMember).where(TeamMember.team_id == x.id, TeamMember.user_id == owner.id)
    )
    session.commit()
    # reading: their default workspace's settings, not the old row's
    assert web.get(URL).json()["rule"] == "all"
    # validating the old workspace's templates: it is no template of theirs
    error_of(web.put(SETTINGS_URL, {"calendar_template_id": secret.id}), 404, "not_found")
    assert "OLD_WORKSPACE_SECRET" not in web.get(URL).text
    # writing: the default workspace's row, never the old one
    assert web.put(SETTINGS_URL, {"rule": "none"}).status_code == 200
    session.expire_all()
    assert session.get(WorkspacePrefs, (x.id, owner.id)).auto_join_rule == "organizer"
    home = teams.ensure_team(session, owner)
    assert workspace_prefs.for_user(session, owner.id, home.id).auto_join_rule == "none"


def test_workspace_for_is_the_workspace_for_a_member_and_the_personal_one_for_anyone_else(
    session, cast
):
    assert workspace_prefs.workspace_for(session, cast.pat.id, cast.x.id) == cast.x.id
    leave(session, cast)
    assert workspace_prefs.workspace_for(session, cast.pat.id, cast.x.id) == cast.personal.id
    assert workspace_prefs.default_template_id(session, cast.x.id, cast.pat.id) is None


# ---- a calendar join filed in a workspace the person left ----------------------------------------


def test_a_join_filed_in_a_workspace_the_person_left_records_into_their_personal_one(
    session, cast, fake_bot
):
    leave(session, cast)
    request = PlacementRequest(
        user_id=cast.pat.id,
        platform="google_meet",
        native_id="abc-defg-hij",
        meeting_url="https://meet.google.com/abc-defg-hij",
        template_id=cast.board.id,  # the old workspace's template: not theirs any more
        source="calendar",
        workspace_team_id=cast.x.id,
    )
    from datetime import UTC, datetime

    meeting = place_bot(session, fake_bot, request, now=datetime.now(UTC), settings=make_settings())
    assert meeting.workspace_team_id == cast.personal.id
    assert meeting.shared_team_id is None
    used = session.get(Template, meeting.template_id)
    assert used.team_id == cast.personal.id
    assert session.scalar(select(Meeting.id).where(Meeting.workspace_team_id == cast.x.id)) is None
