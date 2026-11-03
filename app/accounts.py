"""What a new account starts with, and the sweep of accounts that never confirmed their email.
Sign-up and any script that creates a user call `add_user_defaults` or `create_user_defaults`;
migration 0007 gave the existing owner the same four templates."""

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Template, User, UserSettings, VocabularyTerm

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
    """Delete password accounts that never confirmed their email within 48 hours, which frees
    their slot. Admins are never swept. An unverified account has no meetings, so only its
    templates, vocabulary and settings (and its tokens, by cascade) go with it. Returns how many."""
    ids = list(
        session.scalars(
            select(User.id)
            .where(
                User.email_verified_at.is_(None),
                User.is_admin.is_(False),
                User.created_at < now - UNVERIFIED_GRACE,
            )
            .with_for_update(skip_locked=True)
        )
    )
    if not ids:
        return 0
    session.execute(delete(VocabularyTerm).where(VocabularyTerm.owner_user_id.in_(ids)))
    session.execute(delete(UserSettings).where(UserSettings.user_id.in_(ids)))
    session.execute(delete(Template).where(Template.owner_user_id.in_(ids)))
    session.execute(delete(User).where(User.id.in_(ids)))
    session.commit()
    return len(ids)
