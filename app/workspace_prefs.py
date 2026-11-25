"""What a person sets once per workspace, and what a meeting reads from its workspace (workspaces
design, sections 3 and 12.7; plan Tasks 6 and 7).

Two things live here, both reading ONE workspace:

* `Prefs` and `for_user`: a person's note taker name, auto-join rule, its two modifiers and the
  template their calendar's meetings use, IN one workspace (`workspace_prefs`, one row per
  workspace and person). A person with no row there falls back to their own defaults
  (`user_settings` and `users.bot_name`), so a workspace they joined yesterday behaves as they
  would expect. `Prefs` has the same attribute names as a `UserSettings` row, so
  `JoinSettings.for_user`, the calendar sync and the join runner take it as they took that row.
* `for_meeting` and `terms`: what the pipeline, the translation, the notes rewrite and the mails
  read for a MEETING: the vocabulary and the default template of the meeting's workspace
  (`meetings.workspace_team_id`), never the recorder's own. It keeps working when the recorder has
  left: the workspace outlives them and nothing here reads their account.

Mail (spec 12.7) is sent only if the person's own preference allows it AND, for a workspace-level
mail such as a reminder, the workspace's own setting is on: `mail_allowed` is the one function that
says so.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import teams
from app.models import (
    Meeting,
    Team,
    Template,
    User,
    UserSettings,
    VocabularyTerm,
    WorkspacePrefs,
)

# What a person with no settings row at all gets (sign-up makes one, so this is a safety net).
DEFAULT_RULE = "all"


@dataclass(frozen=True)
class Prefs:
    """One person's settings in one workspace. `bot_name` is the name that applies there: the
    workspace's own, else the person's default (`users.bot_name`), else None (the server default).
    `own_bot_name` is only what was set for this workspace."""

    team_id: int
    user_id: int
    bot_name: str | None
    own_bot_name: str | None
    auto_join_rule: str
    join_unanswered: bool
    skip_one_on_ones: bool
    calendar_template_id: int | None


def _template_of_workspace(session: Session, team_id: int, template_id: int | None) -> int | None:
    """`template_id` if it is a live template of this workspace, else None (a template of another
    workspace, or a deleted one, is no choice here)."""
    if template_id is None:
        return None
    return session.scalar(
        select(Template.id).where(
            Template.id == template_id,
            Template.team_id == team_id,
            Template.deleted_at.is_(None),
        )
    )


def for_user(session: Session, user_id: int, team_id: int) -> Prefs:
    """The person's settings in this workspace: their row there, else their own defaults."""
    row = session.get(WorkspacePrefs, (team_id, user_id))
    default_name = session.scalar(select(User.bot_name).where(User.id == user_id))
    if row is not None:
        return Prefs(
            team_id=team_id,
            user_id=user_id,
            bot_name=row.bot_name or default_name,
            own_bot_name=row.bot_name,
            auto_join_rule=row.auto_join_rule,
            join_unanswered=row.join_unanswered,
            skip_one_on_ones=row.skip_one_on_ones,
            calendar_template_id=_template_of_workspace(session, team_id, row.calendar_template_id),
        )
    own = session.get(UserSettings, user_id)
    return Prefs(
        team_id=team_id,
        user_id=user_id,
        bot_name=default_name,
        own_bot_name=None,
        auto_join_rule=own.auto_join_rule if own is not None else DEFAULT_RULE,
        join_unanswered=bool(own.join_unanswered) if own is not None else False,
        skip_one_on_ones=bool(own.skip_one_on_ones) if own is not None else False,
        calendar_template_id=_template_of_workspace(
            session, team_id, own.calendar_template_id if own is not None else None
        ),
    )


def row_for_update(session: Session, user_id: int, team_id: int) -> WorkspacePrefs:
    """The person's row in this workspace, made from their current effective values when they have
    none yet (so the first change to one field does not reset the others to the model defaults).
    In the caller's transaction, no commit."""
    row = session.get(WorkspacePrefs, (team_id, user_id), with_for_update=True)
    if row is not None:
        return row
    current = for_user(session, user_id, team_id)
    row = WorkspacePrefs(
        team_id=team_id,
        user_id=user_id,
        bot_name=None,
        auto_join_rule=current.auto_join_rule,
        join_unanswered=current.join_unanswered,
        skip_one_on_ones=current.skip_one_on_ones,
        calendar_template_id=current.calendar_template_id,
    )
    session.add(row)
    session.flush()
    return row


def terms(session: Session, team_id: int) -> list[str]:
    """The vocabulary of this workspace, oldest term first: what a meeting filed in it uses."""
    return list(
        session.scalars(
            select(VocabularyTerm.term)
            .where(VocabularyTerm.team_id == team_id)
            .order_by(VocabularyTerm.id)
        )
    )


def workspace_for(session: Session, user_id: int, team_id: int) -> int:
    """The workspace to act in for this person when a row names `team_id` (a calendar connection, a
    scheduled join): that workspace while they are a CURRENT member of it, else their personal one.
    A person who left a workspace never has its templates, vocabulary or settings read on their
    behalf through an old row."""
    if teams.membership(session, user_id, team_id) is not None:
        return team_id
    user = session.get(User, user_id)
    return teams.ensure_personal_team(session, user).id


def default_template_id(session: Session, team_id: int, user_id: int | None = None) -> int | None:
    """The template notes are written with when nothing else was chosen in this workspace: the
    person's own choice for their calendar's meetings there (when they have one and it is still a
    live template of the workspace), else the workspace's oldest live template. For a person who is
    not a member of the workspace: none (nothing of it is used on their behalf)."""
    if user_id is not None and teams.membership(session, user_id, team_id) is None:
        return None
    if user_id is not None:
        chosen = for_user(session, user_id, team_id).calendar_template_id
        if chosen is not None:
            return chosen
    return session.scalar(
        select(Template.id)
        .where(Template.team_id == team_id, Template.deleted_at.is_(None))
        .order_by(Template.id)
        .limit(1)
    )


@dataclass(frozen=True)
class MeetingContext:
    """What a meeting reads from its workspace. `recorder` is None when the recorder's account is
    gone: nothing here needs them."""

    team_id: int
    terms: list[str]
    default_template_id: int | None
    recorder: Prefs | None


def for_meeting(session: Session, meeting: Meeting) -> MeetingContext:
    """The vocabulary and default template of the meeting's workspace, and the recorder's settings
    there (their note taker name, for a resent bot). Never the recorder's personal lists."""
    team_id = meeting.workspace_team_id
    recorder = session.get(User, meeting.owner_user_id)
    return MeetingContext(
        team_id=team_id,
        terms=terms(session, team_id),
        default_template_id=default_template_id(
            session, team_id, recorder.id if recorder is not None else None
        ),
        recorder=for_user(session, recorder.id, team_id) if recorder is not None else None,
    )


def mail_allowed(person_wants: bool, team: Team | None = None, *, workspace_level: bool) -> bool:
    """Spec 12.7: a mail goes only if the PERSON's own preference allows it and, for a
    workspace-level mail (a reminder), the WORKSPACE's setting is on too. A mail about the person's
    own meeting (notes ready, tasks) has no workspace switch: `workspace_level` is False."""
    if not person_wants:
        return False
    if not workspace_level:
        return True
    return team is not None and team.reminders_on
