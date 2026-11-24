"""Who may do what on a team, and the changes the Team screen makes (spec 8.1, 8.4).

Roles are team-scoped (the platform admin, `users.is_admin`, is a different thing and has no route
into team content):

* owner: everything: invite (as admin or member), change roles, remove anyone but themselves, edit
  the team and every alias.
* admin: invite members (not admins), remove members (not admins, not the owner), edit the team and
  every alias.
* member: read the team, edit their own aliases, leave.

A call the caller may not make is 403 `team_forbidden`, but only after the id is known to be on the
caller's own team: someone else's team is 404, never 403. Nothing here commits except where it
says so; routes commit.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tasks, teams, titles, visibility, workspace_context
from app.extraction import normalise
from app.models import Task, Team, TeamInvite, TeamMember, User
from app.services import Refused

MANAGER_ROLES = teams.MANAGER_ROLES
MAX_ALIASES, MAX_ALIAS_CHARS = tasks.MAX_ALIASES, tasks.MAX_ALIAS_CHARS
FORBIDDEN_MESSAGE = "Only an owner or admin of the team can do that."


TEAM_HEADER = workspace_context.TEAM_HEADER
# The `X-Team-Id` header of a route (workspaces design, section 7): see `workspace_context`.
requested_team = workspace_context.requested_team


def forbidden(message: str = FORBIDDEN_MESSAGE) -> Refused:
    return Refused(403, "team_forbidden", message)


def not_found() -> Refused:
    return Refused(404, "not_found", "No such team member.")


def my_membership(session: Session, user: User, team_id: int | None = None) -> TeamMember:
    """The caller's membership of the active workspace: `team_id` (the `X-Team-Id` header), else
    their default workspace (`teams.default_membership`). Naming a team the caller is not in is a
    404, as for any team that is not theirs (`workspace_context.require`)."""
    team = workspace_context.require(session, user, team_id)
    member = teams.membership(session, user.id, team.id)
    if member is None:  # a personal team the account lost its row in: every account has one
        raise Refused(404, "not_found", "No such team.")
    return member


def active_team(session: Session, user: User, team_id: int | None) -> visibility.ActiveTeam:
    """The workspace a LIST (meetings, decisions, review, the sidebar counts) is about: the named
    team (404 `No such team.` when the caller is not in it), else their default workspace. Never
    None: every list narrows. The result goes to `visibility.readable_in` / `in_workspace`."""
    team = workspace_context.require(session, user, team_id)
    return visibility.ActiveTeam(id=team.id, personal=teams.is_personal_of(team, user.id))


def active_team_or_default(
    session: Session, user: User, team_id: int | None
) -> visibility.ActiveTeam:
    """`active_team` for `GET /api/session`, the first call of every page, which must never fail
    over a stale team (the page itself answers a stale `?team=`): a team the caller is not in gives
    their default workspace (`teams.default_membership`) instead of a 404. A route that names no
    team acts on that same default workspace (`workspace_context.require`)."""
    if team_id is None or teams.membership(session, user.id, team_id) is None:
        default = teams.default_membership(session, user.id)
        team_id = default.team_id if default is not None else None
    return active_team(session, user, team_id)


def require_manager(member: TeamMember) -> None:
    if member.role not in MANAGER_ROLES:
        raise forbidden()


def member_of_my_team(session: Session, me: TeamMember, user_id: int) -> TeamMember:
    """Another (or the same) member of the caller's team; 404 for anyone else."""
    found = session.scalars(
        select(TeamMember)
        .join(User, User.id == TeamMember.user_id)
        .where(
            TeamMember.team_id == me.team_id,
            TeamMember.user_id == user_id,
            User.status != "deleted",
        )
    ).first()
    if found is None:
        raise not_found()
    return found


def invite_of_my_team(session: Session, me: TeamMember, invite_id: int) -> TeamInvite:
    found = session.scalars(
        select(TeamInvite).where(
            TeamInvite.id == invite_id,
            TeamInvite.team_id == me.team_id,
            TeamInvite.accepted_at.is_(None),
            TeamInvite.revoked_at.is_(None),
        )
    ).first()
    if found is None:
        raise Refused(404, "not_found", "No such invitation.")
    return found


def can_remove(me: TeamMember, target: TeamMember) -> bool:
    """Leaving is for everyone but the owner (who hands the team over first); removing someone is
    for the owner (anyone else) and for admins (members only)."""
    if target.user_id == me.user_id:
        return me.role != "owner"
    if target.role == "owner":
        return False
    if me.role == "owner":
        return True
    return me.role == "admin" and target.role == "member"


def clean_aliases(values: list[str]) -> list[str]:
    """The list a person typed, as stored: one line each, at most 60 characters, duplicates (by
    how they would be matched) dropped, never more than 20. Plain text."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        alias = " ".join(titles.clean_title(raw, MAX_ALIAS_CHARS).split())
        key = normalise(alias)
        if not alias or not key or key in seen:
            continue
        seen.add(key)
        out.append(alias)
    return out[:MAX_ALIASES]


def update_member(
    session: Session,
    me: TeamMember,
    target: TeamMember,
    *,
    role: str | None,
    aliases: list[str] | None,
) -> None:
    """Apply a role and/or alias change; all of it is refused if any part is not allowed."""
    if role is not None and role != target.role:
        if me.role != "owner" or target.role == "owner":
            raise forbidden("Only the owner of the team can change roles.")
    if aliases is not None:
        if not (me.role in MANAGER_ROLES or target.user_id == me.user_id):
            raise forbidden("You can only change your own names.")
    if role is not None and role != target.role:
        target.role = role
    if aliases is not None:
        cleaned = clean_aliases(aliases)
        _refuse_taken_aliases(session, target, cleaned)
        target.aliases = cleaned
    session.flush()


def _refuse_taken_aliases(session: Session, target: TeamMember, aliases: list[str]) -> None:
    """422 `alias_taken` when an alias is another active member's name (whole or one word of it),
    email name or alias: it would steal the tasks said to be theirs."""
    others = [m for m in tasks.members_of(session, target.team_id) if m.user_id != target.user_id]
    for alias in aliases:
        wanted = normalise(alias)
        words = {w for w in wanted.split() if len(w) >= tasks.MIN_TOKEN_CHARS}
        for other in others:
            names = [
                normalise(other.name or ""),
                normalise(other.email.split("@")[0]),
                *(normalise(a) for a in other.aliases),
            ]
            held = {w for n in names for w in n.split()}
            if wanted in names or (words and words <= held):
                raise Refused(
                    422,
                    "alias_taken",
                    f'"{alias}" already names {other.display_name} in meetings. Use something '
                    "that is only this person.",
                )


def remove_member(session: Session, me: TeamMember, target: TeamMember, now: datetime) -> None:
    """Remove someone, or leave (`target` is the caller). 403 `team_forbidden` when the role does
    not allow it; the owner cannot leave while others remain (409 `owner_cannot_leave`)."""
    if target.user_id == me.user_id and me.role == "owner":
        raise Refused(
            409,
            "owner_cannot_leave",
            "You own this team. Ask the pilot admin to hand it to someone else before you leave.",
        )
    if not can_remove(me, target):
        raise forbidden()
    user = session.get(User, target.user_id)
    try:
        teams.leave(session, user, now, team_id=target.team_id)
    except teams.TeamChangeRefused as exc:
        raise Refused(exc.status, exc.code, exc.message) from None


def open_task_counts(session: Session, team_id: int) -> dict[int, int]:
    """Open (not done) tasks assigned to each member of the team."""
    rows = session.execute(
        select(Task.assignee_user_id, func.count())
        .where(
            Task.team_id == team_id,
            Task.assignee_user_id.is_not(None),
            Task.status != "done",
            Task.deleted_at.is_(None),
            Task.suggested.is_(False),
        )
        .group_by(Task.assignee_user_id)
    )
    return {user_id: count for user_id, count in rows}


def team_of_member(session: Session, member: TeamMember) -> Team:
    return session.get(Team, member.team_id)
