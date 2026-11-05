"""The Team screen's routes (spec 8.4). Included into `app.api.router`, so they share its `/api`
prefix, login check, CSRF rule and error shape. The rules (who may do what) are in
`app/team_admin.py`; the invitations are in `app/team_invites.py`.

Everyone reads their own team; changing it needs the owner or an admin (403 `team_forbidden`). An id
that is not on the caller's own team is 404, never 403.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auth, signup, team_admin, team_invites, titles
from app.api_schemas import (
    AcceptInviteIn,
    ErrorResponse,
    InvitePreview,
    InviteResult,
    InvitesCreated,
    InvitesIn,
    TeamInviteOut,
    TeamMemberOut,
    TeamMemberPatch,
    TeamMembers,
    TeamOut,
    TeamPatch,
)
from app.db import get_session
from app.models import Team, TeamInvite, TeamMember, User
from app.services import Refused

router = APIRouter()

FORBIDDEN = {403: {"model": ErrorResponse, "description": "`team_forbidden`"}}
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Not on the caller's team"}}


def _now() -> datetime:
    return datetime.now(UTC)


def _name(user: User) -> str:
    return titles.clean_title(user.name, 100) or user.email.split("@")[0]


def _team_out(session: Session, me: TeamMember, now: datetime) -> TeamOut:
    team = session.get(Team, me.team_id)
    members = session.scalar(
        select(func.count())
        .select_from(TeamMember)
        .join(User, User.id == TeamMember.user_id)
        .where(TeamMember.team_id == team.id, User.status != "deleted")
    )
    pending = session.scalar(
        select(func.count())
        .select_from(TeamInvite)
        .where(TeamInvite.team_id == team.id, *team_invites.live(now))
    )
    return TeamOut(
        id=team.id,
        name=team.name,
        share_new_meetings=team.share_new_meetings,
        my_role=me.role,
        member_count=members,
        pending_invites=pending,
        can_manage=me.role in team_admin.MANAGER_ROLES,
        can_change_roles=me.role == "owner",
    )


def _invite_out(session: Session, invite: TeamInvite, now: datetime) -> TeamInviteOut:
    inviter = session.get(User, invite.invited_by) if invite.invited_by else None
    return TeamInviteOut(
        id=invite.id,
        email=invite.email,
        name=invite.name,
        role=invite.role,
        status=team_invites.expiry_of(invite, now),
        invited_by=_name(inviter) if inviter else None,
        last_sent_at=invite.last_sent_at,
        expires_at=invite.expires_at,
    )


@router.get("/team", tags=["team"])
def read_team(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> TeamOut:
    """The caller's team: its name, the sharing default, the caller's role and what that role may
    do. Every account has one (a team of one until it joins another)."""
    return _team_out(session, team_admin.my_membership(session, user), _now())


@router.patch("/team", tags=["team"], responses=FORBIDDEN)
def update_team(
    body: TeamPatch,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TeamOut:
    """Rename the team and/or switch "Share new meetings" (owner or admin). The default applies,
    when a meeting's notes are first written, to meetings of the team's owner and admins only; a
    member's meetings are never shared by it. 403 `team_forbidden`."""
    me = team_admin.my_membership(session, user)
    team_admin.require_manager(me)
    team = session.get(Team, me.team_id)
    if body.name is not None:
        name = titles.clean_title(body.name, 100)
        if not name:
            raise Refused(422, "invalid_name", "Give the team a name.")
        team.name = name
    if body.share_new_meetings is not None:
        team.share_new_meetings = body.share_new_meetings
    session.commit()
    return _team_out(session, me, _now())


@router.get("/team/members", tags=["team"])
def list_members(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> TeamMembers:
    """The people on the team, and (for everyone) the invitations still open. Each member carries
    what the caller may do to them, so the screen shows only buttons that work."""
    me = team_admin.my_membership(session, user)
    now = _now()
    open_tasks = team_admin.open_task_counts(session, me.team_id)
    rows = session.execute(
        select(User, TeamMember)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == me.team_id, User.status != "deleted")
        .order_by(TeamMember.joined_at, TeamMember.id)
    ).all()
    members = [
        TeamMemberOut(
            user_id=u.id,
            name=_name(u),
            email=u.email,
            role=m.role,
            is_you=u.id == user.id,
            aliases=list(m.aliases or []),
            open_tasks=open_tasks.get(u.id, 0),
            joined_at=m.joined_at,
            can_change_role=me.role == "owner" and m.role != "owner",
            can_edit_aliases=me.role in team_admin.MANAGER_ROLES or u.id == user.id,
            can_remove=team_admin.can_remove(me, m),
        )
        for u, m in rows
    ]
    invites = session.scalars(
        select(TeamInvite)
        .where(
            TeamInvite.team_id == me.team_id,
            TeamInvite.accepted_at.is_(None),
            TeamInvite.revoked_at.is_(None),
        )
        .order_by(TeamInvite.id)
    )
    return TeamMembers(members=members, invites=[_invite_out(session, i, now) for i in invites])


@router.patch(
    "/team/members/{user_id:id}",
    tags=["team"],
    responses={**FORBIDDEN, **NOT_FOUND},
)
def update_member(
    user_id: int,
    body: TeamMemberPatch,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TeamMembers:
    """Change a member's role (owner only; `admin` or `member`) and/or the names they are matched
    by in meetings (`aliases`, replacing the list: an owner or admin for anyone, a member for
    themselves). All of it is refused if any part is not allowed (403 `team_forbidden`)."""
    me = team_admin.my_membership(session, user)
    target = team_admin.member_of_my_team(session, me, user_id)
    team_admin.update_member(session, me, target, role=body.role, aliases=body.aliases)
    session.commit()
    return list_members(session, user)


@router.delete(
    "/team/members/{user_id:id}",
    status_code=204,
    tags=["team"],
    responses={**FORBIDDEN, **NOT_FOUND, 409: {"model": ErrorResponse}},
)
def remove_member(
    user_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Remove a member, or leave the team (your own id). They get a fresh team of one; the open
    tasks assigned to them become unassigned; their meetings stay theirs and are unshared. An owner
    or admin removes members; only the owner removes an admin; the owner cannot be removed and
    cannot leave while others remain (409 `owner_cannot_leave`). 403 `team_forbidden`."""
    me = team_admin.my_membership(session, user)
    target = team_admin.member_of_my_team(session, me, user_id)
    team_admin.remove_member(session, me, target, _now())
    session.commit()
    return Response(status_code=204)


@router.post(
    "/team/invites",
    tags=["team"],
    responses={
        **FORBIDDEN,
        409: {"model": ErrorResponse, "description": "`pilot_full`, `invite_limit`"},
    },
)
def invite_people(
    body: InvitesIn,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> InvitesCreated:
    """Invite up to 10 people by email (owner or admin; only the owner may invite an admin). One
    mail each, a link that works once for 7 days. A new person's invitation reserves one of the
    pilot's places (409 `pilot_full` when there is none) and bypasses a closed or full pilot for
    that address. Answers per address: `sent`, `resent` (already invited: a new link went out),
    `already_member`, `already_invited` (one went out a minute ago), `invalid_email`. The answer
    is the same for an address with an account and one without (except `pilot_full`, which only
    an address needing a place can get). 409 `invite_limit`: 10 open invitations or 20 a day per
    team."""
    me = team_admin.my_membership(session, user)
    team_admin.require_manager(me)
    if body.role == "admin" and me.role != "owner":
        raise team_admin.forbidden("Only the owner of the team can invite an admin.")
    settings = request.app.state.settings
    now = _now()
    # The same lock sign-up, confirmation and the admin take around "count the places, take one":
    # two managers inviting at once cannot both take the last place.
    session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": signup.SIGNUP_LOCK})
    results: list[InviteResult] = []
    seen: set[str] = set()
    try:
        for invitee in body.invitees:
            address = signup.normalise_email(invitee.email)
            if address is None:
                results.append(
                    InviteResult(
                        email=invitee.email.strip()[:254], status="invalid_email", invite=None
                    )
                )
                continue
            if address in seen:
                continue
            seen.add(address)
            done = team_invites.create(
                session,
                settings,
                me.team_id,
                user,
                email=address,
                name=invitee.name,
                role=body.role,
                now=now,
                has_room=signup.has_room(session, now),
            )
            results.append(
                InviteResult(
                    email=address,
                    status=done.status,
                    invite=_invite_out(session, done.invite, now) if done.invite else None,
                )
            )
    except Refused:
        session.rollback()
        raise
    except IntegrityError:  # the same address invited twice at once
        session.rollback()
        raise Refused(409, "invite_conflict", "That was just invited. Try again.") from None
    session.commit()
    return InvitesCreated(results=results)


@router.post(
    "/team/invites/{invite_id:id}/resend",
    tags=["team"],
    responses={
        **FORBIDDEN,
        **NOT_FOUND,
        409: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
def resend_invite(
    invite_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TeamInviteOut:
    """Send an open (or expired) invitation again: a new link, the old one stops working, 7 more
    days. 429 `too_soon` within a minute of the last mail; 409 `pilot_full` for an expired
    invitation to a new person when the pilot has filled up meanwhile."""
    me = team_admin.my_membership(session, user)
    team_admin.require_manager(me)
    invite = team_admin.invite_of_my_team(session, me, invite_id)
    now = _now()
    session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": signup.SIGNUP_LOCK})
    session.refresh(invite)
    if invite.last_sent_at and now < invite.last_sent_at + team_invites.RESEND_COOLDOWN:
        raise Refused(429, "too_soon", "That invitation was sent a moment ago. Try again shortly.")
    if (
        not team_invites.is_live(invite, now)
        and team_invites.needs_place(session, invite.email)
        and not signup.has_room(session, now)
    ):
        raise Refused(409, "pilot_full", "The pilot has no free places. Ask the pilot admin.")
    team_invites.send(session, request.app.state.settings, invite, user, now)
    session.commit()
    return _invite_out(session, invite, now)


@router.delete(
    "/team/invites/{invite_id:id}",
    status_code=204,
    tags=["team"],
    responses={**FORBIDDEN, **NOT_FOUND},
)
def revoke_invite(
    invite_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Cancel an invitation: its link stops working and its place is free again."""
    me = team_admin.my_membership(session, user)
    team_admin.require_manager(me)
    team_invites.revoke(session, team_admin.invite_of_my_team(session, me, invite_id), _now())
    session.commit()
    return Response(status_code=204)


@router.get(
    "/team/invites/preview",
    tags=["team"],
    responses={400: {"model": ErrorResponse, "description": "`invite_invalid`"}},
)
def preview_invite(token: str, session: Session = Depends(get_session)) -> InvitePreview:
    """What an invitation link is for, so the page can say "Dan invited you to join Product team"
    and send the visitor to sign in (`has_account`) or sign up with the link. Public: the token in
    the query is the credential, and it reveals nothing to anyone who does not hold it. 400
    `invite_invalid` for a wrong, used, expired or revoked link: one answer for all of them."""
    now = _now()
    found = team_invites.find_by_token(session, token, now)
    if found is None:
        raise team_invites.invalid()
    team = session.get(Team, found.team_id)
    inviter = session.get(User, found.invited_by) if found.invited_by else None
    return InvitePreview(
        email=found.email,
        name=found.name,
        role=found.role,
        team_name=team.name,
        inviter_name=team_invites.plain_name(inviter) if inviter else None,
        has_account=not team_invites.needs_place(session, found.email),
        expires_at=found.expires_at,
    )


@router.post(
    "/team/invites/accept",
    tags=["team"],
    responses={
        400: {"model": ErrorResponse, "description": "`invite_invalid`"},
        409: {"model": ErrorResponse, "description": "`already_on_team`"},
    },
)
def accept_invite(
    body: AcceptInviteIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TeamOut:
    """Join the team of an invitation link, signed in with the account the invitation was sent to
    (the same answer for a wrong, used, expired, revoked or someone else's link). Your team of one
    is dissolved: your tasks move to the team you join; your meetings stay yours and private. 409
    `already_on_team` when you are on a team with other people: leave it first. A new person does
    not call this: signing up with the link joins them when their email is confirmed."""
    now = _now()
    invite = team_invites.find_by_token(session, body.token, now)
    if invite is None:
        raise team_invites.invalid()
    try:
        member = team_invites.accept(session, user, invite, now)
    except Refused:
        session.rollback()
        raise
    session.commit()
    return _team_out(session, member, now)
