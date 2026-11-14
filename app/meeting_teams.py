"""A meeting and its team: share, unshare, move, move back (multi-team design 6.3, 6.4, 6.6).

Three states, one team at most:

* private: the recorder's alone (`shared_team_id` and `owner_team_id` null);
* shared: `shared_team_id` is a team the recorder belongs to; its members read, the recorder
  still owns it;
* moved: `owner_team_id` is that team's; the team owns it (every member reads it, the recorder
  while a member, and the team's owner and admins change it with the recorder: `visibility.owns`).
  `shared_team_id` is cleared, so the two never disagree.

The routes call these after finding the meeting through `visibility` (a meeting the caller cannot
read is a 404 before anything here runs). Each function changes the meeting, re-homes its tasks
(`teams.rehome_tasks`) and flags the repeat topics stale; the route commits.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import series as series_rules
from app import teams
from app.models import Meeting, Team, User
from app.services import Refused
from app.visibility import MANAGER_ROLES


def _team_owned() -> Refused:
    return Refused(
        409,
        "meeting_team_owned",
        "This meeting belongs to a team. An owner or admin of the team can move it back first.",
    )


def _not_your_team() -> Refused:
    return Refused(404, "not_found", "No such team.")


def _lock(session: Session, meeting: Meeting) -> None:
    """Take the meeting's row and re-read it: share, unshare, move and move back each decide from
    the meeting's team columns, so two at once (two tabs, a double click) must run one after the
    other, the second seeing what the first did. Held until the route commits."""
    session.refresh(meeting, with_for_update=True)


def _touched(session: Session, meeting: Meeting) -> None:
    teams.rehome_tasks(session, meeting)
    before = meeting.series_id
    series_rules.follow_meeting(session, meeting)  # flag on: into the row of its new scope
    if before is not None:
        series_rules.mark_topics_stale(session, before)  # the repeat topics may change


def _member_team(session: Session, user: User, team_id: int) -> Team:
    """A team the caller belongs to and may put a meeting in: 404 for any other team (it is not
    theirs to know), 409 `personal_team` for their own personal space (it is the recorder's alone:
    sharing or moving there changes nothing)."""
    member = teams.membership(session, user.id, team_id)
    team = session.get(Team, team_id) if member is not None else None
    if team is None:
        raise _not_your_team()
    if team.is_personal:
        raise Refused(
            409,
            "personal_team",
            "Choose a team. Your personal space is only yours; nobody else would see the meeting.",
        )
    return team


def share(session: Session, user: User, meeting: Meeting, team_id: int | None) -> None:
    """Share the meeting with a team (the recorder stays its owner). With a team named, the caller
    must belong to it and it must not be personal. With none named (the old frontend), the default
    team as ever (`teams.default_membership`); someone with two or more ordinary teams must say
    which (422 `team_required`), never quietly their personal one. 409 `meeting_team_owned` for a
    moved meeting."""
    _lock(session, meeting)
    if meeting.owner_team_id is not None:
        raise _team_owned()
    if team_id is None:
        if len(teams.ordinary_teams_of(session, user.id)) >= 2:
            raise Refused(
                422,
                "team_required",
                "You are on more than one team. Choose which team to share this meeting with.",
            )
        team = teams.ensure_team(session, user)
    else:
        team = _member_team(session, user, team_id)
    meeting.shared_team_id = team.id
    _touched(session, meeting)


def unshare(session: Session, meeting: Meeting) -> None:
    """Make the meeting private again. 409 `meeting_team_owned` for a moved meeting."""
    _lock(session, meeting)
    if meeting.owner_team_id is not None:
        raise _team_owned()
    meeting.shared_team_id = None
    _touched(session, meeting)


def move(session: Session, user: User, meeting: Meeting, team_id: int) -> None:
    """Move the meeting to a team the caller (its recorder) belongs to: the team owns it from now
    on, and it stays with the team if the recorder leaves or deletes their account. Doing it again
    for the same team is fine; a meeting the team already owns cannot go to another team except by
    the team's owner or admin moving it back first (409 `already_moved`). Only a finished meeting
    (409 `meeting_not_ready`); 409 `multi_team_disabled` while the switch is off."""
    if not teams.multi_team_enabled():
        raise Refused(
            409, "multi_team_disabled", "Moving a meeting to a team is not available yet."
        )
    team = _member_team(session, user, team_id)
    _lock(session, meeting)
    if meeting.owner_team_id is not None:
        if meeting.owner_team_id == team.id:
            return
        raise Refused(
            409,
            "already_moved",
            "This meeting already belongs to another team. An owner or admin of that team can "
            "move it back first.",
        )
    if meeting.owner_user_id != user.id:  # a team admin cannot move someone else's own meeting
        raise _not_your_team()
    if meeting.status != "ready":
        raise Refused(
            409, "meeting_not_ready", "Wait until the notes are ready, then move the meeting."
        )
    meeting.owner_team_id, meeting.shared_team_id = team.id, None
    _touched(session, meeting)


def move_back(session: Session, user: User, meeting: Meeting) -> None:
    """The team's owner or admin gives the meeting back to its recorder: private to them again.
    403 `team_forbidden` for a member without that role (the recorder cannot pull it back
    alone: the team keeps its history), 409 `not_moved`, 409 `recorder_gone` when the recorder
    deleted their account (there is nobody to give it to)."""
    _lock(session, meeting)
    if meeting.owner_team_id is None:
        raise Refused(409, "not_moved", "This meeting does not belong to a team.")
    role = teams.membership(session, user.id, meeting.owner_team_id)
    if role is None or role.role not in MANAGER_ROLES:
        raise Refused(403, "team_forbidden", "Only an owner or admin of the team can move it back.")
    recorder = session.scalar(select(User.status).where(User.id == meeting.owner_user_id))
    if recorder in (None, "deleted"):
        raise Refused(
            409,
            "recorder_gone",
            "The person who recorded this meeting has left, so it cannot be given back.",
        )
    meeting.owner_team_id = None
    meeting.shared_team_id = None
    _touched(session, meeting)
