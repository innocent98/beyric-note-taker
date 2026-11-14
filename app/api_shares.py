"""The Share notes by email routes and the mail opt-out (spec section 10). Included into
`app.api.router`, so they share its `/api` prefix, login check, CSRF rule and error shape.

Owner only: another account's meeting, and a meeting a teammate may read but did not record, is a
404, never a 403. A request only queues work (the mails, and the translation when the language is
not translated yet); no mail is sent and no model is called in a request.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import auth, mail, services, shares, tokens
from app.api_schemas import (
    ErrorResponse,
    OptOutIn,
    OptOutOut,
    ShareCreated,
    ShareIn,
    ShareList,
    ShareOut,
    ShareRecipientOut,
)
from app.db import get_session
from app.models import User

router = APIRouter()


def _out(share, recipients) -> ShareOut:
    return ShareOut(
        share_id=share.id,
        status=share.status,
        language=share.language,
        attach=share.attach,
        include_transcript=share.include_transcript,
        message=share.message,
        created_at=share.created_at,
        recipients=[
            ShareRecipientOut(
                email=r.email, name=r.name, status=r.status, is_team_member=r.user_id is not None
            )
            for r in recipients
        ],
    )


SHARE_RESPONSES = {
    202: {"description": "Queued: the mails go within a minute, or after the translation."},
    403: {"model": ErrorResponse, "description": "`email_not_verified`"},
    404: {"model": ErrorResponse, "description": "No such meeting (or not the caller's)"},
    409: {"model": ErrorResponse, "description": "`not_ready`: the notes are not written yet"},
    422: {
        "model": ErrorResponse,
        "description": "`language_unsupported`, `invalid_recipient`, `no_recipients`, "
        "`too_many_recipients` (more than 10)",
    },
    429: {
        "model": ErrorResponse,
        "description": "`share_limit`: 20 recipients a day per account; `translation_limit`",
    },
    503: {"model": ErrorResponse, "description": "`mail_unavailable`: no mail provider"},
}


@router.post(
    "/meetings/{meeting_id:id}/shares",
    status_code=202,
    tags=["shares"],
    responses=SHARE_RESPONSES,
)
def create_share(
    meeting_id: int,
    body: ShareIn,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> ShareCreated:
    """Mail the notes of a ready meeting to up to 10 people, each in their own mail, sent as the
    caller (`Reply-To` is the caller, the footer names them). The mail holds the Summary,
    Decisions, Action items and Open questions, abridged; a PDF or Word file with every section
    can be attached, with the transcript if ticked. Nobody outside the caller's team gets a link
    to the notes. A language that is not translated yet is translated first: the answer is still
    202 and `status` is `waiting_translation`. Limits: 10 recipients a share and 20 a day per
    account (429 `share_limit`). Needs a confirmed email. Only the person who recorded the meeting
    (a team admin of a meeting moved to the team gets 404: the mail goes out as its sender)."""
    meeting = services.recorded_meeting_or_404(session, user, meeting_id)
    if not mail.is_configured(request.app.state.settings):
        raise services.Refused(
            503, "mail_unavailable", "Email is not available right now. Try again later."
        )
    share = shares.create_share(
        session,
        user,
        meeting,
        [shares.RecipientIn(r.email, r.name) for r in body.recipients],
        language=body.language,
        attach=body.attach,
        include_transcript=body.include_transcript,
        message=body.message,
    )
    return ShareCreated(share_id=share.id, status=share.status)


@router.get(
    "/meetings/{meeting_id:id}/shares",
    tags=["shares"],
    responses={404: {"model": ErrorResponse}},
)
def list_shares(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> ShareList:
    """The shares of a meeting, newest first, for "Shared with ...": who each went to and whether
    it was sent. Only the person who recorded the meeting sees them, even on a meeting moved to a
    team (its owners and admins get 404: the addresses are the recorder's)."""
    meeting = services.recorded_meeting_or_404(session, user, meeting_id)
    return ShareList(shares=[_out(s, r) for s, r in shares.shares_of(session, meeting)])


@router.post(
    "/mail/opt-out",
    tags=["shares"],
    responses={400: {"model": ErrorResponse, "description": "`token_invalid_or_expired`"}},
)
def opt_out(body: OptOutIn, session: Session = Depends(get_session)) -> OptOutOut:
    """Stop mail from Beyric Note Taker to the address a shared-notes mail was sent to. Public:
    the token in the mail's footer link is the credential. The page the link opens must show a
    button that calls this (mail scanners open links, and a GET must not opt anyone out). The
    token works once, for 180 days. Mail that lets a person into their own account (sign-in,
    password reset) is not stopped."""
    token = tokens.consume(session, body.token, "mail_optout", mail.utcnow())
    if token is None:
        raise services.Refused(400, "token_invalid_or_expired", "This link is not valid any more.")
    mail.suppress(session, token.email, "unsubscribed")
    session.commit()
    return OptOutOut(status="unsubscribed")
