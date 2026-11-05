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

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app import ai_runs
from app.extraction import (
    ExtractionFailed,
    ParsedNotes,
    check_extraction,
    extract,
    fingerprint,
    parse_notes,
)
from app.models import Decision, Meeting, OpenQuestion, ReviewItem, User
from app.visibility import can_read_clause

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_GAP = timedelta(minutes=5)
STALE_RUNNING = timedelta(minutes=15)
BACKFILL_TASK_DAYS = 14
# Until users have a timezone (the team layer, W0.4) due dates are read in UTC.
DEFAULT_TIMEZONE = "UTC"


def mark_pending(meeting: Meeting) -> None:
    """Queue the meeting for extraction (new notes were written). The caller commits."""
    meeting.extraction_status = "pending"
    meeting.extraction_attempts = 0
    meeting.extraction_attempted_at = None


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
    parsed: ParsedNotes,
    client,
) -> dict | None:
    """The `heard_as` words by please-check index, or None when the call failed (the failure is
    recorded in `ai_runs`, whatever the outcome: the call cost something). Only the please-check
    bullets are asked about in W1: action items are the Tasks phase's."""
    asked = ParsedNotes([], [], [], parsed.please_check)
    model = None
    try:
        call = extract(asked, transcript, day, DEFAULT_TIMEZONE, client=client)
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
            prompt_version=ai_runs.EXTRACT_REVIEW_PROMPT_VERSION,
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
            prompt_version=ai_runs.EXTRACT_REVIEW_PROMPT_VERSION,
        )
        return None
    ai_runs.record(
        session,
        meeting_id,
        "extract",
        call.model,
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        prompt_version=ai_runs.EXTRACT_REVIEW_PROMPT_VERSION,
    )
    return {item.index: item.heard_as for item in checked.review}


def _configured_model() -> str:
    from app.config import get_settings

    return get_settings().extract_model


def run_extraction(
    session: Session,
    meeting: Meeting,
    *,
    client=None,
    now: datetime | None = None,
    with_ai: bool = True,
) -> str | None:
    """Read the meeting's notes into decisions, open questions and review items. Returns the
    meeting's `extraction_status` afterwards, or None for a meeting that is not ready.

    The lists are written first and kept whatever the model does. `with_ai=False` is the backfill
    for an old meeting: decisions only, nothing else, no model call, status `skipped`.

    Nothing is held across the model call: the claim is committed as `running`, the lists are
    committed, and only then is the model asked. The result is written with a compare-and-set on
    `running` and this attempt's time. If the owner rewrote the notes meanwhile
    (`mark_pending` clears both), the answer belongs to the old notes and is dropped: the meeting
    stays `pending` and the next pass reads the new notes.
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
    day = (meeting.created_at or stamp).date()
    meeting.extraction_status = "running"
    meeting.extraction_attempts += 1
    meeting.extraction_attempted_at = stamp
    attempts = meeting.extraction_attempts
    session.commit()  # also lets go of the claim's row lock

    _reconcile(session, Decision, meeting_id, parsed.decisions)
    _reconcile(session, OpenQuestion, meeting_id, parsed.open_questions)
    _reconcile(session, ReviewItem, meeting_id, parsed.please_check, owner_user_id=owner_id)
    session.commit()  # no row lock or open transaction while the model is being asked

    heard = {}
    if parsed.please_check:
        heard = _ask_the_model(session, meeting_id, transcript, day, parsed, client)
    if heard is not None:
        status = "done"
        values = {
            "extraction_status": "done",
            "extraction_version": ai_runs.EXTRACT_REVIEW_PROMPT_VERSION,
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
    if heard is not None and parsed.please_check:  # the answer is final: no answer means no hint
        hints = {fingerprint(text): heard.get(i) for i, text in enumerate(parsed.please_check)}
        for row in session.scalars(select(ReviewItem).where(ReviewItem.meeting_id == meeting_id)):
            if row.fingerprint in hints:
                row.heard_as = hints[row.fingerprint]
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
    counts = {"queued": 0, "decisions_only": 0}
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
            return counts
        after = meeting.id
        if meeting.created_at >= recent_after:
            mark_pending(meeting)
            session.commit()
            counts["queued"] += 1
        else:
            run_extraction(session, meeting, now=now, with_ai=False)
            counts["decisions_only"] += 1


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
