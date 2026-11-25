"""Deleting a workspace (workspaces design, sections 7 and 12.3).

Only the owner, and only when nobody else is in it any more. What it held (tasks, projects, series,
integrations, invitations, calendar connections, preferences, notifications) goes with it by the
foreign keys. Its meetings are not the owner's to destroy:

* a meeting whose recorder still has an account goes back to that recorder: private, filed in
  their personal workspace, the tasks and date proposals read from it re-filed there too
  (`deletion.give_back_meeting`, the same rule account deletion uses for a team that goes);
* a meeting whose recorder's account is gone has nobody to return it to: it is deleted, recording
  and all, as any meeting is (`services.delete_meeting`).

A billing change never reaches this module: a lapsed plan or a downgrade keeps every workspace
(`teams.create_owned_team` only refuses making more).
"""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import deletion, services, teams
from app.models import Meeting, Team, TeamMember, User
from app.services import Refused


def _others(session: Session, team_id: int, user_id: int) -> int:
    """Members other than `user_id` whose account is still there (a deleted account's row is waiting
    for its purge and does not count as someone in the team)."""
    return session.scalar(
        select(func.count())
        .select_from(TeamMember)
        .join(User, User.id == TeamMember.user_id)
        .where(
            TeamMember.team_id == team_id,
            TeamMember.user_id != user_id,
            User.status != "deleted",
        )
    )


def _check(session: Session, team_id: int, user: User) -> Team:
    """The team to delete, or 404 `not_found` (not a member: it is not theirs to know), 409
    `personal_team`, 403 `team_forbidden` (not the owner), 409 `team_has_members`."""
    member = teams.membership(session, user.id, team_id)
    team = session.get(Team, team_id) if member is not None else None
    if team is None:
        raise Refused(404, "not_found", "No such team.")
    if team.is_personal:
        raise Refused(409, "personal_team", "Your personal space cannot be deleted.")
    if team.owner_user_id != user.id:
        raise Refused(403, "team_forbidden", "Only the owner of the team can delete it.")
    if _others(session, team_id, user.id):
        raise Refused(
            409,
            "team_has_members",
            "Other people are still in this team. Remove them first: you can delete it once you "
            "are its only member.",
        )
    return team


def delete_team(session: Session, provider, team_id: int, user: User) -> None:
    """Delete workspace `team_id` for its owner `user`. Commits. Meetings whose recorder is gone are
    deleted one at a time first (each re-checks under the team's lock and commits; 409
    `meeting_active` or 502 `recording_delete_failed` stop it with the team still there, and a
    retry finishes); then, under the lock and with the checks repeated, the rest is given back and
    the team goes."""
    _check(session, team_id, user)
    filed_here = or_(
        Meeting.workspace_team_id == team_id,
        Meeting.owner_team_id == team_id,
        Meeting.shared_team_id == team_id,
    )
    hollow = select(User.id).where(User.status == "deleted")
    doomed = list(
        session.scalars(
            select(Meeting.id)
            .where(filed_here, Meeting.owner_user_id.in_(hollow))
            .order_by(Meeting.id)
        )
    )
    for meeting_id in doomed:
        # Each deletion is authorised on its own, under the team's lock, right before it happens
        # (`delete_meeting` commits, which releases the lock): a person who joined meanwhile stops
        # the rest, and nothing is destroyed for a team that is no longer the owner's alone.
        teams._lock_teams(session, team_id)
        _check(session, team_id, user)
        meeting = session.get(Meeting, meeting_id)
        if meeting is not None:
            services.delete_meeting(session, provider, meeting)
    teams._lock_teams(session, team_id)
    team = _check(session, team_id, user)  # the same checks again for the give-back and the delete
    for meeting in session.scalars(select(Meeting).where(filed_here).order_by(Meeting.id)):
        deletion.give_back_meeting(session, meeting)
    session.flush()
    # The owner's own calendar connection in it goes with the provider clean-up (the grant is
    # revoked unless another workspace of theirs uses the account; the Meet subscription is
    # stopped), parked for the route to settle after the commit
    # (`calendar_connect.settle_released`).
    from app import calendar_connect  # it imports this package's `teams`

    calendar_connect.detach_workspace(session, user.id, team_id)
    teams.dissolve_team(session, team)
    session.commit()
