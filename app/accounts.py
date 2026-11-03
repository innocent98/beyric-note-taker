"""What a new account starts with, and the sweep of accounts that never confirmed their email.
Sign-up and any script that creates a user call `add_user_defaults` or `create_user_defaults`;
migration 0007 gave the existing owner the same four templates."""

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models import (
    AuthToken,
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


def sweep_unverified(session: Session, now: datetime) -> int:
    """Delete password accounts that never confirmed their email within 48 hours of their LAST
    submission (the newest verification link, else creation). Admins are never swept. An
    unverified account has no meetings, so only its templates, vocabulary and settings (and its
    tokens, by cascade) go with it. A person who came in with an invitation is put back on the
    waiting list, so the place is not lost with the account. Returns how many."""
    last_submission = func.coalesce(
        select(func.max(AuthToken.created_at))
        .where(AuthToken.user_id == User.id, AuthToken.purpose == "verify_email")
        .scalar_subquery(),
        User.created_at,
    )
    rows = list(
        session.execute(
            select(User.id, User.email)
            .where(
                User.email_verified_at.is_(None),
                User.is_admin.is_(False),
                last_submission < now - UNVERIFIED_GRACE,
            )
            .with_for_update(skip_locked=True, of=User)
        )
    )
    if not rows:
        return 0
    ids, emails = [r[0] for r in rows], [r[1] for r in rows]
    session.execute(
        update(WaitlistEntry)
        .where(WaitlistEntry.email.in_(emails), WaitlistEntry.status == "joined")
        .values(status="waiting", invite_token_id=None)
    )
    session.execute(delete(VocabularyTerm).where(VocabularyTerm.owner_user_id.in_(ids)))
    session.execute(delete(UserSettings).where(UserSettings.user_id.in_(ids)))
    session.execute(delete(Template).where(Template.owner_user_id.in_(ids)))
    session.execute(delete(User).where(User.id.in_(ids)))
    session.commit()
    return len(ids)
