"""Invitations to join a team (spec 8.1, W3).

A token is 32 random bytes in the link; `team_invites` keeps only its SHA-256 hash
(`app/tokens.py`), so a copy of the table cannot be used to join anyone's team. An invitation
works once (accepting marks it in the same statement that checks it, so two requests cannot both
win), until it expires (7 days) or is revoked; "resend" replaces the token, so the old link dies,
and extends the expiry.

Three kinds of person are invited, and the same link serves all three (the page decides):

* **A new person** signs up with the link (`signup.sign_up`'s `invite_token`, or Google, which
  proves the address): the invitation bypasses `closed` and `full` for that address and holds one
  of the pilot's places from the moment it is sent (`reserving_count`, added to
  `signup.slots_taken`: decision D2). They join the team when their email is confirmed.
* **An existing account** signs in and accepts (`accept`). Their team of one is dissolved into the
  one they join (`teams.join`); someone on a team with other people must leave it first.
* **An address nobody opens** costs a place until the link expires or is revoked: at most
  `MAX_PENDING` live invitations per team and `MAX_PER_DAY` created per team per day.

An invitation never says whether an address has an account: the answers are the same for both.
Nothing here commits: the caller's transaction decides.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.orm import Session

from app import mail, teams, titles, tokens
from app.config import Settings
from app.models import AuthToken, TeamInvite, TeamMember, User, WaitlistEntry
from app.services import Refused

TTL = timedelta(days=7)
MAX_PENDING = 10  # live invitations a team may hold (the whole pilot is ten accounts)
MAX_PER_DAY = 20  # invitations a team may create in 24 hours, revoked ones included
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_NAME_CHARS = 100

INVALID = (400, "invite_invalid", "That invitation is not valid any more. Ask for a new one.")


def invalid() -> Refused:
    return Refused(*INVALID)


def live(now: datetime):
    """The `where` terms of a live invitation."""
    return (
        TeamInvite.accepted_at.is_(None),
        TeamInvite.revoked_at.is_(None),
        TeamInvite.expires_at > now,
    )


def is_live(invite: TeamInvite, now: datetime) -> bool:
    return invite.accepted_at is None and invite.revoked_at is None and invite.expires_at > now


def find_by_token(session: Session, raw: str | None, now: datetime) -> TeamInvite | None:
    """The live invitation for this link, without using it up."""
    if not tokens.well_formed(raw):
        return None
    return session.scalars(
        select(TeamInvite).where(TeamInvite.token_hash == tokens.digest(raw), *live(now))
    ).first()


def live_for_email(session: Session, email: str, now: datetime) -> TeamInvite | None:
    """A live invitation sent to this address (the newest). The Google path proves the address, so
    it needs no token in the URL."""
    return session.scalars(
        select(TeamInvite)
        .where(TeamInvite.email == email, *live(now))
        .order_by(TeamInvite.id.desc())
    ).first()


def reserving_count(session: Session, now: datetime) -> int:
    """How many of the pilot's places live invitations hold: distinct addresses with a live
    invitation that are not already counted another way (a confirmed account, an account made
    from a waiting-list invitation, a live waiting-list invitation). Added to `slots_taken`, so
    it needs no counter that could drift."""
    counted_account = exists().where(
        User.email == TeamInvite.email,
        User.status != "deleted",
        or_(
            User.is_admin.is_(True),
            User.email_verified_at.is_not(None),
            User.email.in_(select(WaitlistEntry.email).where(WaitlistEntry.status == "joined")),
        ),
    )
    waitlist_live = exists().where(
        AuthToken.purpose == "waitlist_invite",
        AuthToken.email == TeamInvite.email,
        AuthToken.used_at.is_(None),
        AuthToken.expires_at > now,
    )
    return session.scalar(
        select(func.count(func.distinct(TeamInvite.email))).where(
            *live(now), ~counted_account, ~waitlist_live
        )
    )


def needs_place(session: Session, email: str) -> bool:
    """Whether inviting this address would take a place of the pilot: it has no account yet."""
    found = session.scalars(select(User.id).where(User.email == email, User.status != "deleted"))
    return found.first() is None


# ---- creating, resending, revoking ----


def _clean_name(name: str | None) -> str | None:
    cleaned = " ".join(titles.clean_title(name or "", MAX_NAME_CHARS).split())
    return cleaned or None


def _link(settings: Settings, raw: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/accept-invite?token={raw}"


def plain_name(person: User) -> str | None:
    """Their name when it is plainly a name (it goes in a subject line), else None."""
    from app.mail_templates import PLAIN_NAME

    name = (person.name or "").strip()
    return name if PLAIN_NAME.fullmatch(name) else None


def send(session: Session, settings: Settings, invite: TeamInvite, inviter: User, now: datetime):
    """Queue the "Team invite" mail with a fresh token (the old link stops working) and a full
    7 days. The raw token lives in the mail payload only until the mail is sent (app/mail.py)."""
    raw, digest = tokens.mint()
    invite.token_hash, invite.expires_at, invite.last_sent_at = digest, now + TTL, now
    mail.enqueue(
        session,
        invite.email,
        "team_invite",
        {"link": _link(settings, raw), "inviter": plain_name(inviter) or ""},
    )


@dataclass(frozen=True)
class InviteResult:
    email: str
    status: str  # sent | resent | already_member | already_invited | invalid_email
    invite: TeamInvite | None = None


def create(
    session: Session,
    settings: Settings,
    team_id: int,
    inviter: User,
    *,
    email: str,
    name: str | None,
    role: str,
    now: datetime,
    has_room: bool,
) -> InviteResult:
    """Invite one (already normalised) address. `has_room` is the pilot's answer, asked by the
    caller before this and once per address. 409 `pilot_full` for someone who needs a place when
    there is none; 409 `invite_limit` over the team's caps. In the caller's transaction."""
    already = session.scalars(
        select(TeamMember.id)
        .join(User, User.id == TeamMember.user_id)
        .where(TeamMember.team_id == team_id, User.email == email, User.status != "deleted")
    ).first()
    if already is not None:
        return InviteResult(email, "already_member")
    existing = session.scalars(
        select(TeamInvite).where(
            TeamInvite.team_id == team_id,
            TeamInvite.email == email,
            TeamInvite.accepted_at.is_(None),
            TeamInvite.revoked_at.is_(None),
        )
    ).first()
    if existing is not None:
        # Asking again for an address that is already invited sends the mail again (a new link)
        # unless one went out a moment ago.
        if existing.last_sent_at and now < existing.last_sent_at + RESEND_COOLDOWN:
            return InviteResult(email, "already_invited", existing)
        if is_live(existing, now):
            existing.role, existing.name = role, _clean_name(name) or existing.name
        else:  # an expired one is reused, and may hold a place again: check below
            existing.role, existing.name = role, _clean_name(name)
            if needs_place(session, email) and not has_room:
                raise _full()
        send(session, settings, existing, inviter, now)
        return InviteResult(email, "resent", existing)
    pending = session.scalar(
        select(func.count())
        .select_from(TeamInvite)
        .where(TeamInvite.team_id == team_id, *live(now))
    )
    created = session.scalar(
        select(func.count())
        .select_from(TeamInvite)
        .where(TeamInvite.team_id == team_id, TeamInvite.created_at > now - timedelta(days=1))
    )
    if pending >= MAX_PENDING or created >= MAX_PER_DAY:
        raise Refused(
            409,
            "invite_limit",
            "You have reached the limit for invitations. Revoke one, or try again tomorrow.",
        )
    if needs_place(session, email) and not has_room:
        raise _full()
    invite = TeamInvite(
        team_id=team_id,
        email=email,
        name=_clean_name(name),
        role=role,
        invited_by=inviter.id,
        token_hash=tokens.mint()[1],  # a placeholder: `send` puts the mailed token's hash in
        expires_at=now + TTL,
        created_at=now,
    )
    session.add(invite)
    send(session, settings, invite, inviter, now)
    session.flush()
    return InviteResult(email, "sent", invite)


def _full() -> Refused:
    return Refused(
        409,
        "pilot_full",
        "The pilot has no free places. Ask the pilot admin.",
    )


def revoke(session: Session, invite: TeamInvite, now: datetime) -> None:
    """Stop an invitation (its link no longer works and its place is free)."""
    if invite.accepted_at is None and invite.revoked_at is None:
        invite.revoked_at = now
        session.flush()


# ---- accepting ----


def _check_person(user: User, invite: TeamInvite) -> None:
    if user.status != "active" or user.email_verified_at is None or user.email != invite.email:
        raise invalid()


def accept(session: Session, user: User, invite: TeamInvite, now: datetime) -> TeamMember:
    """`user` joins the invitation's team. The invitation is used up in the same statement that
    checks it is still live, so two requests cannot both win; if joining is refused (409
    `already_on_team`) it is left unused. The address must be the account's own and confirmed.
    In the caller's transaction, no commit."""
    _check_person(user, invite)
    with session.begin_nested():
        won = session.scalars(
            update(TeamInvite)
            .where(TeamInvite.id == invite.id, *live(now))
            .values(accepted_at=now, claimed_user_id=user.id)
            .returning(TeamInvite.id)
        ).first()
        if won is None:
            raise invalid()
        session.refresh(invite)
        try:
            return teams.join(session, user, invite.team_id, invite.role, _alias(invite), now)
        except teams.TeamChangeRefused as exc:
            raise Refused(exc.status, exc.code, exc.message) from None


def _alias(invite: TeamInvite) -> str | None:
    """What the inviter called them, kept as a way the meetings may name them."""
    return invite.name


def claim(session: Session, invite: TeamInvite, user: User) -> None:
    """Remember that this account was made from the invitation, to join when confirmed."""
    invite.claimed_user_id = user.id


def accept_confirmed(session: Session, user: User, now: datetime) -> bool:
    """For a NEW account whose address has just been confirmed (by its mailed link, or by Google,
    which proves the address): join the team of the live invitation sent to that address, if any.
    Safe because the account is brand new and the mailbox is proven. Never fails the confirmation:
    when joining is refused the invitation stays for the person to accept by hand. True when they
    joined. An account that already existed accepts by hand (`accept`): joining dissolves
    its team of one, which it must choose."""
    invite = session.scalars(
        select(TeamInvite)
        .where(
            TeamInvite.email == user.email,
            or_(TeamInvite.claimed_user_id == user.id, TeamInvite.claimed_user_id.is_(None)),
            *live(now),
        )
        .order_by(TeamInvite.id.desc())
    ).first()
    if invite is None:
        return False
    try:
        accept(session, user, invite, now)
    except Refused:
        return False
    return True


def expiry_of(invite: TeamInvite, now: datetime) -> str:
    return "pending" if is_live(invite, now) else "expired"
