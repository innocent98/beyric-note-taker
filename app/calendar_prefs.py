"""The auto-join settings of one person IN ONE WORKSPACE: the single reader the calendar uses.

Workspaces design, section 3: the rule, the two boxes ("also join invites I haven't answered yet",
"skip one-on-one meetings") and the template for calendar meetings belong to the person's
connection in a workspace (`workspace_prefs`, one row per workspace and person). A person with no
row in a workspace falls back to their own defaults (`user_settings`), and a person with neither
gets the stand-in below (rule `all`, nothing else ticked).

Every place that decides whether a bot joins, or shows what the rule says, reads the settings
here, so the rule of one workspace can never be applied to another's calendar. Slice W2 owns the
`WorkspacePrefs` writers and `JoinSettings.for_user`; this module is the one function the calendar
needs from them and returns an object with the same four attributes whichever table it came from.
"""

from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models import UserSettings, WorkspacePrefs

# What a user without a settings row gets (sign-up creates one, so this is a safety net).
DEFAULT_USER_SETTINGS = SimpleNamespace(
    auto_join_rule="all",
    calendar_template_id=None,
    join_unanswered=False,
    skip_one_on_ones=False,
)


def for_workspace(session: Session, user_id: int, team_id: int):
    """The person's auto-join settings in workspace `team_id`: their `WorkspacePrefs` row there,
    else their own `user_settings`, else the stand-in. Read-only: it never creates a row."""
    row = session.get(WorkspacePrefs, (team_id, user_id))
    if row is not None:
        return row
    return session.get(UserSettings, user_id) or DEFAULT_USER_SETTINGS


def for_update(session: Session, user_id: int, team_id: int) -> WorkspacePrefs:
    """The person's `WorkspacePrefs` row in `team_id`, made from what they would get now (their
    defaults) when they have none there, so a write to one workspace starts from what that
    workspace showed and never touches another's. In the caller's transaction (adds, no commit)."""
    row = session.get(WorkspacePrefs, (team_id, user_id))
    if row is not None:
        return row
    base = session.get(UserSettings, user_id) or DEFAULT_USER_SETTINGS
    row = WorkspacePrefs(
        team_id=team_id,
        user_id=user_id,
        auto_join_rule=base.auto_join_rule,
        join_unanswered=base.join_unanswered,
        skip_one_on_ones=base.skip_one_on_ones,
        calendar_template_id=base.calendar_template_id,
    )
    session.add(row)
    return row
