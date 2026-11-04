"""What a new account starts with, and the sweep of accounts that never confirmed their email.
Sign-up and any script that creates a user call `add_user_defaults` or `create_user_defaults`;
migration 0007 gave the existing owner the same four templates."""

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models import (
    MailOutbox,
    Template,
    User,
    UserSettings,
    VocabularyTerm,
    WaitlistEntry,
)

# The four templates migration 0001 seeded, as (name, instructions). Each new user gets their
# own copies: templates are never shared (spec section 0, Q13). A test keeps this equal to the
# migration's list.
DEFAULT_TEMPLATES = [
    (
        "General",
        "Write a summary, key points, decisions, action items with owners, and open questions.",
    ),
    (
        "Client call",
        "Focus on what the client asked for, commitments made, deadlines, concerns raised, "
        "and next steps. Group action items by who owns them.",
    ),
    (
        "Team update",
        "Group by person or workstream: progress, blockers, and what is planned next. "
        "List decisions and action items at the end.",
    ),
    (
        "Interview",
        "Summarise the candidate's background, strengths, concerns, notable answers, "
        "and any follow-up questions. Do not give a hire or no-hire verdict.",
    ),
]


def add_user_defaults(session: Session, user: User) -> list[Template]:
    """Add `user`'s own copy of the default templates and a settings row whose calendar template
    is their "General", in the caller's transaction (no commit; `user` must have an id)."""
    templates = [
        Template(owner_user_id=user.id, name=name, instructions=instructions)
        for name, instructions in DEFAULT_TEMPLATES
    ]
    session.add_all(templates)
    session.flush()
    session.add(UserSettings(user_id=user.id, calendar_template_id=templates[0].id))
    return templates


def create_user_defaults(session: Session, user: User) -> list[Template]:
    """`add_user_defaults`, then commit. Call once per user."""
    templates = add_user_defaults(session, user)
    session.commit()
    return templates


UNVERIFIED_GRACE = timedelta(hours=48)
ABANDONED_AFTER = timedelta(hours=1)  # at the unconfirmed cap, rows older than this make room


def purge_unverified(
    session: Session, now: datetime, grace: timedelta, *, include_invited: bool = True
) -> int:
    """Delete unconfirmed password accounts whose LAST SIGN-UP SUBMISSION (`terms_accepted_at`,
    set at every submission, else creation) is older than `grace`. A resend does not count: it
    would let one address keep a row alive for ever. Admins are never touched. An unconfirmed
    account has no meetings, so only its templates, vocabulary and settings (and its tokens, by
    cascade) go with it. Someone who came in with an invitation goes back on the waiting list so
    the place is not lost with the account; `include_invited=False` leaves those rows alone.
    Does not commit (the sign-up lock is held by callers). Returns how many."""
    last_submission = func.coalesce(User.terms_accepted_at, User.created_at)
    query = select(User.id, User.email).where(
        User.email_verified_at.is_(None),
        User.is_admin.is_(False),
        last_submission < now - grace,
    )
    if not include_invited:
        query = query.where(
            User.email.not_in(select(WaitlistEntry.email).where(WaitlistEntry.status == "joined"))
        )
    rows = list(session.execute(query.with_for_update(skip_locked=True, of=User)))
    if not rows:
        return 0
    return delete_unverified(session, [r[0] for r in rows], [r[1] for r in rows])


def delete_unverified(session: Session, ids: list[int], emails: list[str]) -> int:
    """Delete these unconfirmed accounts (ids and their emails) with their templates, vocabulary
    and settings; someone who came in with an invitation goes back on the waiting list. Does not
    commit. The callers (the sweep, the sign-up flood defence) have chosen rows that are unconfirmed
    and not admins."""
    session.execute(
        update(WaitlistEntry)
        .where(WaitlistEntry.email.in_(emails), WaitlistEntry.status == "joined")
        .values(status="waiting", invite_token_id=None)
    )
    # Its verification mail, if not sent yet: the link died with the account's tokens, and sending
    # it would only spend the mail budget and puzzle the person.
    session.execute(
        delete(MailOutbox).where(
            MailOutbox.to_email.in_(emails),
            MailOutbox.status == "pending",
            MailOutbox.template == "verify_email",
        )
    )
    session.execute(delete(VocabularyTerm).where(VocabularyTerm.owner_user_id.in_(ids)))
    session.execute(delete(UserSettings).where(UserSettings.user_id.in_(ids)))
    session.execute(delete(Template).where(Template.owner_user_id.in_(ids)))
    session.execute(delete(User).where(User.id.in_(ids)))
    return len(ids)


def sweep_unverified(session: Session, now: datetime) -> int:
    """The hourly sweep: unconfirmed accounts 48 hours after their last submission. Commits."""
    count = purge_unverified(session, now, UNVERIFIED_GRACE)
    session.commit()
    return count
