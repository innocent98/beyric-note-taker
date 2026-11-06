"""Team integrations (W10): connecting, disconnecting and using the Slack webhook.

One row per team and kind (`integrations`). Only an owner or admin connects or disconnects
(`team_admin.require_manager`, in the routes). The secret is decrypted here, for the one call that
needs it, and nowhere else. Posting never raises into the notes pipeline: `notify_meeting` catches
everything, records a failure on the row (a fixed sentence, shown on the card) and returns.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import slack, tasks, titles, vault
from app.models import Integration, IntegrationPost, Meeting, Task, User

logger = logging.getLogger(__name__)

SLACK = "slack_webhook"
HOUR = timedelta(hours=1)
# Per team and hour, counting every post (a meeting's, and the test made when connecting).
POSTS_PER_HOUR = 30
TESTS_PER_HOUR = 5


def _owner(team_id: int, kind: str) -> str:
    return f"team:{team_id}:{kind}"


def get(session: Session, team_id: int, kind: str = SLACK) -> Integration | None:
    return session.scalar(
        select(Integration).where(Integration.team_id == team_id, Integration.kind == kind)
    )


def _recent_posts(
    session: Session, team_id: int, now: datetime, *, tests_only: bool = False
) -> int:
    query = select(func.count(IntegrationPost.id)).where(
        IntegrationPost.team_id == team_id, IntegrationPost.created_at > now - HOUR
    )
    if tests_only:
        query = query.where(IntegrationPost.meeting_id.is_(None))
    return session.scalar(query) or 0


def connect_slack(
    session: Session, team_id: int, user: User, url: str, now: datetime, *, sender=None
) -> Integration:
    """Check the pasted webhook, post one test message to it, and store it (encrypted). A webhook
    Slack does not accept is not stored. 422 `slack_webhook_invalid`, 429 `integration_rate`,
    502 `slack_unreachable`, 503 `integrations_unavailable`."""
    from app.services import Refused  # not at the top: services imports the worker's modules

    try:
        clean = slack.check_webhook(url)
    except slack.WebhookInvalid:
        raise Refused(
            422,
            "slack_webhook_invalid",
            "That is not a Slack webhook address. It starts with https://hooks.slack.com/services/.",
        ) from None
    if _recent_posts(session, team_id, now, tests_only=True) >= TESTS_PER_HOUR:
        raise Refused(429, "integration_rate", "Too many tries. Wait a little and try again.")
    try:
        blob, version = vault.encrypt(clean, purpose="integration", owner=_owner(team_id, SLACK))
    except vault.VaultError:
        raise Refused(
            503, "integrations_unavailable", "Connecting is not available right now."
        ) from None
    session.add(IntegrationPost(team_id=team_id, kind=SLACK, meeting_id=None))
    session.commit()  # the attempt counts even if the post fails
    try:
        (sender or slack.post)(clean, slack.test_message())
    except slack.SlackError as exc:
        raise Refused(502, "slack_unreachable", slack.TEXT[exc.reason]) from None
    session.execute(
        insert(Integration)
        .values(
            team_id=team_id,
            kind=SLACK,
            config_enc=blob,
            key_version=version,
            status="connected",
            connected_by=user.id,
        )
        .on_conflict_do_update(
            constraint="uq_integrations_team_kind",
            set_={
                "config_enc": blob,
                "key_version": version,
                "status": "connected",
                "last_error": None,
                "last_error_at": None,
                "connected_by": user.id,
            },
        )
    )
    session.commit()
    return get(session, team_id)


def disconnect(session: Session, team_id: int, kind: str = SLACK) -> None:
    """Delete the row, and with it the stored secret. Idempotent."""
    row = get(session, team_id, kind)
    if row is not None:
        session.delete(row)
        session.commit()


def _fail(session: Session, row: Integration, reason: str, now: datetime) -> None:
    row.status, row.last_error, row.last_error_at = "failing", slack.TEXT[reason], now
    session.commit()


def notify_meeting(
    session: Session,
    meeting: Meeting,
    team_id: int,
    live: list[Task],
    members: list[tasks.Member],
    now: datetime | None = None,
    *,
    sender=None,
) -> bool:
    """Post a meeting's new tasks to the team's Slack, once. True when a post went out. Never
    raises: a failure is written on the integration row and logged by type only."""
    stamp = now or datetime.now(UTC)
    try:
        row = get(session, team_id)
        fresh = [t for t in live if t.deleted_at is None and t.title]
        if row is None or not fresh:
            return False
        # Only a meeting shared with the team: a private meeting's tasks are not visible to
        # teammates (`tasks.visible`), so they must not reach the team's channel either.
        if meeting.shared_team_id != team_id:
            return False
        if _recent_posts(session, team_id, stamp) >= POSTS_PER_HOUR:
            return False  # the cap: this meeting is skipped, not queued
        claimed = session.execute(
            insert(IntegrationPost)
            .values(team_id=team_id, kind=SLACK, meeting_id=meeting.id)
            .on_conflict_do_nothing()
            .returning(IntegrationPost.id)
        ).scalar()
        session.commit()
        if claimed is None:
            return False  # this meeting was already posted
        names = {m.user_id: m.display_name for m in members}
        lines = [
            (t.title, names.get(t.assignee_user_id) or t.spoken_owner or None)
            for t in sorted(fresh, key=lambda t: t.position)
        ]
        text = slack.message(meeting.id, titles.meeting_name(meeting), lines)
        try:
            url = vault.decrypt(
                row.config_enc, row.key_version, purpose="integration", owner=_owner(team_id, SLACK)
            ).reveal()
        except vault.VaultError:
            _fail(session, row, "rejected", stamp)
            return False
        try:
            (sender or slack.post)(url, text)
        except slack.SlackError as exc:
            _fail(session, row, exc.reason, stamp)
            return False
        row.status, row.last_error, row.last_error_at, row.last_posted_at = (
            "connected",
            None,
            None,
            stamp,
        )
        session.commit()
        return True
    except Exception as exc:  # never into the notes pipeline; the type only, no text
        logger.error("slack notify failed type=%s", type(exc).__name__)
        try:
            session.rollback()
        except Exception:
            pass
        return False
