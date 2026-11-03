"""What a new account starts with. Sign-up (a later change) and any script that creates a user
call `create_user_defaults`; migration 0007 gave the existing owner the same four templates."""

from sqlalchemy.orm import Session

from app.models import Template, User, UserSettings

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


def create_user_defaults(session: Session, user: User) -> list[Template]:
    """Give `user` their own copy of the default templates and a settings row whose calendar
    template is their "General". Commits. Call once per user."""
    templates = [
        Template(owner_user_id=user.id, name=name, instructions=instructions)
        for name, instructions in DEFAULT_TEMPLATES
    ]
    session.add_all(templates)
    session.flush()
    session.add(UserSettings(user_id=user.id, calendar_template_id=templates[0].id))
    session.commit()
    return templates
