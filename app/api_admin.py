"""The admin API under `/api/admin/`: users and their usage, pilot settings, the waitlist and
failed mail. Included into `app.api.router`.

Every route depends on `require_admin`: a visitor gets 401 from the guard, a signed-in user who is
not an admin gets 403 `forbidden`, and `tests/test_admin.py` lists every route to prove it. There
is no route for an admin to read another user's meetings, notes or calendar.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app import admin, auth, usage
from app.api_schemas import (
    AdminDeleting,
    AdminInvite,
    AdminMailFailure,
    AdminMailFailures,
    AdminPilot,
    AdminPilotIn,
    AdminResetLink,
    AdminUser,
    AdminUserList,
    AdminUserPatch,
    AdminWaitlist,
    AdminWaitlistEntry,
    ErrorResponse,
)
from app.db import get_session
from app.models import User
from app.services import MAX_ID, Refused

LIST_DEFAULT, LIST_MAX = 50, 100


def require_admin(user: User = Depends(auth.current_user)) -> User:
    if not user.is_admin:
        raise Refused(403, "forbidden", "This is for the administrator only.")
    return user


router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_admin)],
    tags=["admin"],
    responses={403: {"model": ErrorResponse, "description": "Not an admin"}},
)


def _now() -> datetime:
    return datetime.now(UTC)


def _user_out(session: Session, request: Request, user: User) -> AdminUser:
    settings = request.app.state.settings
    return AdminUser(
        id=user.id,
        email=user.email,
        name=user.name,
        is_admin=user.is_admin,
        status=user.status,
        email_verified=user.email_verified_at is not None,
        has_password=user.password_hash is not None,
        has_google=user.google_sub is not None,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        used_seconds=usage.hours_used(session, user, _now(), settings.max_poll_hours * 3600),
        cap_seconds=usage.effective_cap(session, user),
        hours_cap_override=user.hours_cap_seconds,
    )


# ---- users ----


@router.get("/users")
def list_users(
    request: Request,
    limit: int = Query(LIST_DEFAULT, ge=1, le=LIST_MAX),
    before_id: int | None = Query(None, ge=1, le=MAX_ID),
    session: Session = Depends(get_session),
) -> AdminUserList:
    """Accounts with their recorded hours, newest first. Accounts being deleted are not listed."""
    rows = admin.list_users(session, limit + 1, before_id)
    page = rows[:limit]
    return AdminUserList(
        users=[_user_out(session, request, user) for user in page],
        has_more=len(rows) > limit,
        next_before_id=page[-1].id if len(rows) > limit else None,
    )


@router.patch(
    "/users/{user_id:id}",
    responses={code: {"model": ErrorResponse} for code in (404, 409)},
)
def patch_user(
    user_id: int,
    body: AdminUserPatch,
    request: Request,
    session: Session = Depends(get_session),
) -> AdminUser:
    """Suspend or reactivate an account (`status`) and set or clear one user's hours override
    (`hours_cap_seconds`). Only the fields sent change. A suspended user is signed out on their
    next request. 409 `last_admin`: the only active admin cannot be suspended."""
    sent = body.model_dump(exclude_unset=True)
    if not sent:
        raise Refused(422, "validation_error", "Send `status` or `hours_cap_seconds`.")
    user = admin.user_or_404(session, user_id)
    admin.update_user(session, user, sent)
    return _user_out(session, request, user)


@router.delete(
    "/users/{user_id:id}",
    status_code=202,
    responses={code: {"model": ErrorResponse} for code in (404, 409)},
)
def delete_user(user_id: int, session: Session = Depends(get_session)) -> AdminDeleting:
    """Delete an account and everything of theirs. The user is signed out and their slot is free
    at once; the worker then removes their data (bots stopped, recordings deleted at Vexa and
    here). The hours ledger keeps seconds only. 409 `last_admin`."""
    admin.schedule_deletion(session, admin.user_or_404(session, user_id))
    return AdminDeleting(status="deleting")


@router.post(
    "/users/{user_id:id}/reset-link",
    responses={code: {"model": ErrorResponse} for code in (404, 409)},
)
def reset_link(
    user_id: int, request: Request, session: Session = Depends(get_session)
) -> AdminResetLink:
    """A one-time password-reset link (30 minutes) for the admin to give the person, for when
    mail is not working. Shown once, never mailed, and replaces any earlier reset link. 409
    `user_not_verified` or `account_suspended`."""
    user = admin.user_or_404(session, user_id)
    return AdminResetLink(
        reset_url=admin.reset_link(session, request.app.state.settings, user, _now())
    )


# ---- the waitlist ----


@router.get("/waitlist")
def list_waitlist(
    limit: int = Query(LIST_DEFAULT, ge=1, le=LIST_MAX),
    before_id: int | None = Query(None, ge=1, le=MAX_ID),
    session: Session = Depends(get_session),
) -> AdminWaitlist:
    """The waiting list, oldest first, without the people removed. `before_id` is the id to
    continue after (the last `next_before_id`)."""
    rows = admin.list_waitlist(session, limit + 1, before_id)
    page = rows[:limit]
    return AdminWaitlist(
        entries=[AdminWaitlistEntry.model_validate(row, from_attributes=True) for row in page],
        has_more=len(rows) > limit,
        next_before_id=page[-1].id if len(rows) > limit else None,
    )


@router.post(
    "/waitlist/{waitlist_id:id}/invite",
    responses={code: {"model": ErrorResponse} for code in (404, 409)},
)
def invite(
    waitlist_id: int, request: Request, session: Session = Depends(get_session)
) -> AdminInvite:
    """Reserve a place for this person for 7 days and invite them. With mail working the link
    is emailed (`emailed: true`, `invite_url: null`); without it `invite_url` is returned for the
    admin to send. Inviting again replaces the earlier link. 409 `pilot_full` when no place is
    free, `waitlist_entry_closed` for someone who joined or was removed."""
    entry = admin.entry_or_404(session, waitlist_id)
    emailed, url, expires = admin.invite(session, request.app.state.settings, entry, _now())
    return AdminInvite(emailed=emailed, invite_url=url, expires_at=expires)


@router.delete(
    "/waitlist/{waitlist_id:id}",
    status_code=204,
    responses={404: {"model": ErrorResponse}},
)
def remove_waitlist_entry(waitlist_id: int, session: Session = Depends(get_session)) -> Response:
    """Take someone off the list. A live invitation is cancelled and its place freed."""
    admin.remove_entry(session, admin.entry_or_404(session, waitlist_id), _now())
    return Response(status_code=204)


# ---- pilot settings ----


@router.get("/pilot")
def admin_read_pilot(session: Session = Depends(get_session)) -> AdminPilot:
    return AdminPilot(**admin.pilot(session, _now()))


@router.put("/pilot")
def admin_write_pilot(body: AdminPilotIn, session: Session = Depends(get_session)) -> AdminPilot:
    """Open or close sign-up, set the number of accounts (`null` = no limit), the default recorded
    hours per account in seconds (`null` = no cap) and whether password sign-up is on. Every field
    is required."""
    return AdminPilot(**admin.set_pilot(session, body.model_dump(), _now()))


# ---- mail ----


@router.get("/mail-failures")
def mail_failures(session: Session = Depends(get_session)) -> AdminMailFailures:
    """The most recent mail that could not be sent (never with its link)."""
    return AdminMailFailures(
        failures=[
            AdminMailFailure.model_validate(row, from_attributes=True)
            for row in admin.mail_failures(session)
        ]
    )
