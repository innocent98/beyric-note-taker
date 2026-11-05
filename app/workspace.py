"""Decisions, open questions and review items: reading them out of a meeting's notes and keeping
them in step with the notes (spec sections 3.1 to 3.3 and 5).

The lists come from the notes' bullets by code (`app/extraction.py`), so the screens can never
disagree with the notes tab. One model call per meeting adds only `heard_as` to review items, and
only when the transcript has that word. The extraction never fails a meeting: `extraction_status`
says where it stands, a failure is retried on later worker passes (three attempts, a gap between
them) and a meeting stuck in `running` by a crash is taken back after a while.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app import ai_runs, filing, mail, projects, tasks, teams, timezones, titles
from app.extraction import (
    CheckedExtraction,
    ExtractionFailed,
    ModelExtraction,
    ParsedNotes,
    check_extraction,
    extract,
    fingerprint,
    parse_notes,
)
from app.models import Decision, Meeting, OpenQuestion, ReviewItem, User, UserSettings
from app.visibility import can_read_clause

logger = logging.getLogger(__name__)

_EMPTY = ModelExtraction()

MAX_ATTEMPTS = 3
RETRY_GAP = timedelta(minutes=5)
STALE_RUNNING = timedelta(minutes=15)
BACKFILL_TASK_DAYS = 14
# Until users have a timezone (the team layer, W0.4) due dates are read in UTC.
DEFAULT_TIMEZONE = "UTC"


def mark_pending(meeting: Meeting, *, quiet: bool = False) -> None:
    """Queue the meeting for extraction (new notes were written). The caller commits. `quiet` is
    for the backfill and the on-demand button: the tasks it makes are not New and nobody is
    mailed. New notes (a rewrite) are never quiet: they are new."""
    meeting.extraction_status = "pending"
    meeting.extraction_attempts = 0
    meeting.extraction_attempted_at = None
    meeting.extraction_quiet = quiet


def _reconcile(session: Session, model, meeting_id: int, texts: list[str], **new_fields) -> dict:
    """Make the meeting's rows of `model` match `texts`, keyed by fingerprint: a row whose text is
    still there stays (so a decision someone hid is still hidden, a review item they checked is
    still checked), a vanished one is removed, a new one is added. Returns the rows by
    fingerprint."""
    existing = {
        row.fingerprint: row
        for row in session.scalars(select(model).where(model.meeting_id == meeting_id))
    }
    wanted = {fingerprint(text): (position, text) for position, text in enumerate(texts)}
    for key, row in existing.items():
        if key not in wanted:
            session.delete(row)
    kept = {}
    for key, (position, text) in wanted.items():
        row = existing.get(key)
        if row is None:
            row = model(
                meeting_id=meeting_id, text=text, position=position, fingerprint=key, **new_fields
            )
            session.add(row)
        else:
            row.text, row.position = text, position
        kept[key] = row
    session.flush()
    return kept


def _ask_the_model(
    session: Session,
    meeting_id: int,
    transcript: str,
    day: date,
    time_zone: str,
    parsed: ParsedNotes,
    roster: list[str],
    client,
    *,
    full: bool,
) -> CheckedExtraction | None:
    """The checked answer, or None when the call failed (the failure is recorded in `ai_runs`,
    whatever the outcome: the call cost something). With `full` the action items are asked about
    too (the Tasks call, `extract-v1`); without it only the please-check bullets
    (`extract-review-v1`, when the owner has switched tasks off or there are no action items)."""
    asked = parsed if full else ParsedNotes([], [], [], parsed.please_check)
    version = ai_runs.EXTRACT_PROMPT_VERSION if full else ai_runs.EXTRACT_REVIEW_PROMPT_VERSION
    model = None
    try:
        call = extract(asked, transcript, day, time_zone, roster, client=client, full=full)
        model = call.model
        checked = check_extraction(call.result, asked, transcript, day)
    except ExtractionFailed as exc:
        ai_runs.record(
            session,
            meeting_id,
            "extract",
            model or _configured_model(),
            input_tokens=exc.input_tokens,
            output_tokens=exc.output_tokens,
            status="failed",
            error=exc,
            prompt_version=version,
        )
        return None
    except Exception as exc:  # whatever else goes wrong must not fail the meeting either
        logger.warning("meeting %s: extraction error (%s)", meeting_id, type(exc).__name__)
        ai_runs.record(
            session,
            meeting_id,
            "extract",
            _configured_model(),
            status="failed",
            error=exc,
            prompt_version=version,
        )
        return None
    ai_runs.record(
        session,
        meeting_id,
        "extract",
        call.model,
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        prompt_version=version,
    )
    return checked


def _configured_model() -> str:
    from app.config import get_settings

    return get_settings().extract_model


def owner_prefs(session: Session, owner_id: int) -> tuple[bool, bool, str]:
    """The meeting owner's Settings that shape an extraction: whether action items become tasks,
    whether assignees are emailed, and the owner's time zone (UTC until chosen). Defaults are on
    for an account with no settings row."""
    row = session.execute(
        select(
            UserSettings.auto_create_tasks, UserSettings.email_assignees, UserSettings.time_zone
        ).where(UserSettings.user_id == owner_id)
    ).first()
    if row is None:
        return True, True, DEFAULT_TIMEZONE
    return row[0], row[1], row[2] or DEFAULT_TIMEZONE


def run_extraction(
    session: Session,
    meeting: Meeting,
    *,
    client=None,
    now: datetime | None = None,
    with_ai: bool = True,
) -> str | None:
    """Read the meeting's notes into decisions, open questions, review items and (when the owner
    has tasks on) tasks. Returns the meeting's `extraction_status` afterwards, or None for a
    meeting that is not ready.

    The lists, and the tasks as the notes parser reads them, are written first and kept whatever
    the model does. `with_ai=False` is the backfill for an old meeting: decisions only, nothing
    else, no model call, status `skipped`.

    Nothing is held across the model call: the claim is committed as `running`, the lists are
    committed, and only then is the model asked. The result is written with a compare-and-set on
    `running` and this attempt's time. If the owner rewrote the notes meanwhile
    (`mark_pending` clears both), the answer belongs to the old notes and is dropped: the meeting
    stays `pending` and the next pass reads the new notes. The tasks the model refined and the
    "New tasks assigned" mail ride in the same transaction as that compare-and-set, so a dropped
    answer mails nobody.
    """
    if meeting.status != "ready" or not meeting.notes:
        return None
    meeting_id, owner_id = meeting.id, meeting.owner_user_id
    parsed = parse_notes(meeting.notes)
    if not with_ai:
        _reconcile(session, Decision, meeting_id, parsed.decisions)
        meeting.extraction_status = "skipped"
        session.commit()
        return "skipped"

    stamp = now or datetime.now(UTC)
    transcript = meeting.speaker_transcript or meeting.transcript or ""
    make_tasks, mail_assignees, time_zone = owner_prefs(session, owner_id)
    created = meeting.created_at or stamp
    day = timezones.localise(created, time_zone)[0].date()
    quiet = meeting.extraction_quiet
    meeting.extraction_status = "running"
    meeting.extraction_attempts += 1
    meeting.extraction_attempted_at = stamp
    attempts = meeting.extraction_attempts
    session.commit()  # also lets go of the claim's row lock

    _reconcile(session, Decision, meeting_id, parsed.decisions)
    _reconcile(session, OpenQuestion, meeting_id, parsed.open_questions)
    _reconcile(session, ReviewItem, meeting_id, parsed.please_check, owner_user_id=owner_id)
    team_id, members = None, []
    if make_tasks:
        team_id = teams.ensure_team(session, session.get(User, owner_id)).id
        members = tasks.members_of(session, team_id)
        # What the notes parser can read of each action bullet, so a failed call still leaves tasks.
        from_notes = check_extraction(
            _EMPTY, ParsedNotes([], parsed.action_items, [], []), transcript, day
        )
        tasks.reconcile(
            session, meeting, team_id, parsed.action_items, from_notes.action_items, members, day,
            quiet=quiet, refresh=False,
        )  # fmt: skip
    session.commit()  # no row lock or open transaction while the model is being asked

    full = make_tasks and bool(parsed.action_items)
    checked = None
    if full or parsed.please_check:
        roster = [m.display_name for m in members]
        checked = _ask_the_model(
            session, meeting_id, transcript, day, time_zone, parsed, roster, client, full=full
        )
    answered = checked is not None or not (full or parsed.please_check)
    if answered:
        status = "done"
        values = {
            "extraction_status": "done",
            "extraction_version": (
                ai_runs.EXTRACT_PROMPT_VERSION
                if make_tasks
                else ai_runs.EXTRACT_REVIEW_PROMPT_VERSION
            ),
        }
    else:
        status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
        values = {"extraction_status": status}
    won = session.execute(
        update(Meeting)
        .where(
            Meeting.id == meeting_id,
            Meeting.extraction_status == "running",
            Meeting.extraction_attempted_at == stamp,
        )
        .values(**values)
    ).rowcount
    if not won:
        session.commit()  # keeps the ledger row; the answer itself is dropped
        session.expire(meeting)
        return meeting.extraction_status
    if checked is not None and parsed.please_check:  # the answer is final: no answer means no hint
        hints = {
            fingerprint(text): {r.index: r.heard_as for r in checked.review}.get(i)
            for i, text in enumerate(parsed.please_check)
        }
        for row in session.scalars(select(ReviewItem).where(ReviewItem.meeting_id == meeting_id)):
            if row.fingerprint in hints:
                row.heard_as = hints[row.fingerprint]
    if checked is not None and full:
        live = tasks.reconcile(
            session, meeting, team_id, parsed.action_items, checked.action_items, members, day,
            quiet=quiet,
        )  # fmt: skip
        # Dates the meeting stated for a launch or deadline: proposals only, a person confirms.
        projects.propose(
            session, meeting, team_id, checked.milestones,
            filing.team_projects(session, team_id), titles.meeting_name(meeting),
        )  # fmt: skip
        # Not for a meeting older than the outbox keeps its sent rows: the unique index that makes
        # "once" true would be gone, so a rewrite that long after could mail the same people twice.
        recent = stamp - created < mail.KEEP_SENT_FOR
        if answered and mail_assignees and not quiet and recent:
            mail.queue_tasks_assigned(session, meeting, tasks.new_assignees(live, owner_id))
    session.commit()
    session.expire(meeting)
    return status


def claim_next(session: Session, now: datetime) -> Meeting | None:
    """Lock the next meeting that is due for extraction: `pending` and past its retry gap, or
    `running` since long enough that the worker must have died. A meeting out of attempts is
    marked `failed` instead of being taken. Skips rows another worker holds, and meetings of an
    account being deleted."""
    while True:
        meeting = session.scalars(
            select(Meeting)
            .where(
                Meeting.status == "ready",
                Meeting.owner_user_id.not_in(select(User.id).where(User.status == "deleted")),
                or_(
                    and_(
                        Meeting.extraction_status == "pending",
                        or_(
                            Meeting.extraction_attempted_at.is_(None),
                            Meeting.extraction_attempted_at <= now - RETRY_GAP,
                        ),
                    ),
                    and_(
                        Meeting.extraction_status == "running",
                        Meeting.extraction_attempted_at <= now - STALE_RUNNING,
                    ),
                ),
            )
            .order_by(Meeting.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if meeting is None:
            return None
        if meeting.extraction_attempts < MAX_ATTEMPTS:
            return meeting
        meeting.extraction_status = "failed"
        session.commit()


def process_next(session: Session, now: datetime | None = None, *, client=None) -> int | None:
    """One worker step: extract the next due meeting. Returns its id, or None if none was due."""
    now = now or datetime.now(UTC)
    meeting = claim_next(session, now)
    if meeting is None:
        session.rollback()
        return None
    meeting_id = meeting.id
    run_extraction(session, meeting, client=client, now=now)
    return meeting_id


def backfill(session: Session, now: datetime, *, task_days: int = BACKFILL_TASK_DAYS) -> dict:
    """Bring meetings that were ready before extraction existed up to date (spec 3.3, D5).

    Decisions for every one of them, read straight from the notes. Open questions and review
    items (which needs the model) only for meetings of the last `task_days` days, queued for the
    worker, which does them a pass at a time. Meetings that already have a status are left alone,
    so running it twice does nothing the second time. Nothing is ever mailed.
    """
    recent_after = now - timedelta(days=task_days)
    counts = {"queued": 0, "decisions_only": 0, "tasks_queued": 0}
    after = 0
    while True:
        meeting = session.scalars(
            select(Meeting)
            .where(
                Meeting.id > after,
                Meeting.status == "ready",
                Meeting.notes.is_not(None),
                Meeting.extraction_status.is_(None),
                Meeting.owner_user_id.not_in(select(User.id).where(User.status == "deleted")),
            )
            .order_by(Meeting.id)
            .limit(1)
        ).first()
        if meeting is None:
            counts["tasks_queued"] = backfill_tasks(session, now, task_days=task_days)
            return counts
        after = meeting.id
        if meeting.created_at >= recent_after:
            mark_pending(meeting, quiet=True)
            session.commit()
            counts["queued"] += 1
        else:
            run_extraction(session, meeting, now=now, with_ai=False)
            counts["decisions_only"] += 1


def backfill_tasks(session: Session, now: datetime, *, task_days: int = BACKFILL_TASK_DAYS) -> int:
    """Queue, quietly, the recent meetings that were read before tasks existed (their extraction
    finished with the review-only prompt) so the worker makes their tasks. Meetings
    older than `task_days` are left for the on-demand button, and an owner who has switched tasks
    off is left alone. Quiet means the tasks are not New and nobody is mailed (spec 3.3). Returns
    how many were queued; a second run queues none."""
    recent_after = now - timedelta(days=task_days)
    wants_tasks = ~exists().where(
        UserSettings.user_id == Meeting.owner_user_id, UserSettings.auto_create_tasks.is_(False)
    )
    queued, after = 0, 0
    while True:
        meeting = session.scalars(
            select(Meeting)
            .where(
                Meeting.id > after,
                Meeting.status == "ready",
                Meeting.notes.is_not(None),
                Meeting.created_at >= recent_after,
                Meeting.extraction_status == "done",
                Meeting.extraction_version.is_distinct_from(ai_runs.EXTRACT_PROMPT_VERSION),
                Meeting.owner_user_id.not_in(select(User.id).where(User.status == "deleted")),
                wants_tasks,
            )
            .order_by(Meeting.id)
            .limit(1)
        ).first()
        if meeting is None:
            return queued
        after = meeting.id
        mark_pending(meeting, quiet=True)
        session.commit()
        queued += 1


# ---- what the Decisions and Review screens read ----

DECISIONS_DEFAULT_MEETINGS, DECISIONS_MAX_MEETINGS = 20, 50
MAX_SEARCH_CHARS = 200
REVIEW_LIMIT = 200


def _contains(text: str):
    """An ILIKE pattern for "contains `text`", with %, _ and \\ matched literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_decisions(
    session: Session, user: User, query: str | None, limit: int, before_id: int | None
) -> tuple[list[tuple[Meeting, list[Decision]]], bool]:
    """The decisions of the meetings the caller may read (their own and those shared with their
    team: `visibility`), newest meeting first, grouped by meeting, hidden ones left out.

    A search matches the decision's words or its meeting's title (the calendar title, or the
    meeting code); a title match brings every decision of that meeting. Returns the groups of up
    to `limit` meetings and whether there are more; `before_id` is the last meeting id of the
    previous page."""
    shown = [can_read_clause(user), Decision.hidden_at.is_(None)]
    if query and query.strip():
        pattern = _contains(query.strip())
        shown.append(
            or_(
                Decision.text.ilike(pattern, escape="\\"),
                func.coalesce(Meeting.title, "").ilike(pattern, escape="\\"),
                Meeting.native_id.ilike(pattern, escape="\\"),
            )
        )
    meeting_ids = select(Decision.meeting_id).join(Meeting).where(*shown)
    page = select(Meeting).where(Meeting.id.in_(meeting_ids))
    if before_id is not None:
        page = page.where(Meeting.id < before_id)
    meetings = list(session.scalars(page.order_by(Meeting.id.desc()).limit(limit + 1)))
    more, meetings = len(meetings) > limit, meetings[:limit]
    by_meeting: dict[int, list[Decision]] = {m.id: [] for m in meetings}
    decisions = session.scalars(
        select(Decision)
        .join(Meeting)
        .where(*shown, Decision.meeting_id.in_(by_meeting))
        .order_by(Decision.meeting_id, Decision.position)
    )
    for decision in decisions:
        by_meeting[decision.meeting_id].append(decision)
    return [(m, by_meeting[m.id]) for m in meetings], more


def hide_decision(session: Session, user: User, decision_id: int) -> bool:
    """Take a decision out of the log (it is hidden, not edited, and stays hidden when the notes
    are rewritten). False when the caller has no such decision."""
    decision = session.scalars(
        select(Decision)
        .join(Meeting)
        .where(Decision.id == decision_id, Meeting.owner_user_id == user.id)
    ).first()
    if decision is None:
        return False
    if decision.hidden_at is None:
        decision.hidden_at = datetime.now(UTC)
        session.commit()
    return True


def open_review_count(session: Session, user: User) -> int:
    return session.scalar(
        select(func.count())
        .select_from(ReviewItem)
        .where(ReviewItem.owner_user_id == user.id, ReviewItem.checked_at.is_(None))
    )


def list_review(
    session: Session, user: User, checked: bool
) -> tuple[list[tuple[ReviewItem, Meeting]], bool]:
    """The caller's open (or checked) review items, newest meeting first, in notes order. At
    most `REVIEW_LIMIT` come back; the flag says whether there were more."""
    state = ReviewItem.checked_at.is_not(None) if checked else ReviewItem.checked_at.is_(None)
    rows = session.execute(
        select(ReviewItem, Meeting)
        .join(Meeting, Meeting.id == ReviewItem.meeting_id)
        .where(ReviewItem.owner_user_id == user.id, state)
        .order_by(ReviewItem.meeting_id.desc(), ReviewItem.position)
        .limit(REVIEW_LIMIT + 1)
    ).all()
    return [(item, meeting) for item, meeting in rows[:REVIEW_LIMIT]], len(rows) > REVIEW_LIMIT


def review_item(session: Session, user: User, item_id: int) -> tuple[ReviewItem, Meeting] | None:
    """One of the caller's review items with its meeting, or None (also for someone else's)."""
    row = session.execute(
        select(ReviewItem, Meeting)
        .join(Meeting, Meeting.id == ReviewItem.meeting_id)
        .where(ReviewItem.id == item_id, ReviewItem.owner_user_id == user.id)
    ).first()
    return (row[0], row[1]) if row else None


def set_checked(session: Session, item: ReviewItem, checked: bool) -> None:
    """Mark the item checked (keeping the first time if it already is) or open again."""
    item.checked_at = (item.checked_at or datetime.now(UTC)) if checked else None
    session.commit()
