"""Rebuild one finished meeting's transcript and notes from the Skribby segments kept for it.

Run it where the app's `.env` is AND where the persistent audio volume is mounted (the `worker`
container), with the meeting's id:

    docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py 37
    docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py 37 --apply
    docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py \\
        --restore /srv/data/audio/rebuild-backups/meeting-37-....json --apply

The first line is the dry run (look only), the second does it, the third undoes it.

What it does. The app keeps what Skribby sent for a meeting for 7 days (`skribby_segment_evidence`).
This reads that row and builds the transcript and the speaker-labelled transcript through the same
functions the live `TRANSCRIPT_SOURCE=skribby` path uses (`skribby._segments` with the current
speaker-naming rules, `transcript_source.candidate`, the same "too short" checks and the same
vocabulary pass), then writes the notes again from the new text with the meeting's own template
(`notes.generate_notes`, as the pipeline and the rewrite button do). The meeting's
`transcript_source` becomes `skribby-rebuilt`, which is also how a second run knows it has
nothing to do.

The default is a DRY RUN: it reads, prints counts (never a word anybody said, never a name) and
changes nothing. With `--apply` it first saves the old transcript, speaker transcript and notes to a
private JSON file, then changes the meeting in ONE transaction. `--restore FILE --apply` puts them
back.

What it never does: touch any other meeting; change `recorded_seconds`, the hours ledger
(`usage_entries`), sharing, the team or the series; send the "notes ready" mail or any other mail;
post to Slack or Linear; call Skribby or delete anything there. It refuses a meeting that is not
`ready` (being recorded, processing or failed) and one whose lists are being read right now.

The decisions, open questions, review items and tasks of the meeting are read from the notes by the
worker (`workspace.run_extraction`), keyed by each bullet's wording. This script queues that re-read
QUIET (`mark_pending(quiet=True)`: no "New" badge, no assignee mail, no Slack or Linear post) and it
stops, unless `--force`, when a person has touched an item that the new notes would lose
(`docs/sop/2026-11-16-rebuild-meeting-from-evidence.md`).

The backup holds what people said: it lives only on the server's audio volume, is never to be posted
anywhere public, and is not deleted with the meeting or the account, so delete it once the result is
checked.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, or_, select, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import ai_runs, bot_names, pipeline, skribby, transcript_source, workspace  # noqa: E402
from app import tasks as task_rules  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.extraction import ParsedNotes, fingerprint, parse_notes  # noqa: E402
from app.models import (  # noqa: E402
    AiRun,
    Decision,
    Meeting,
    OpenQuestion,
    ReviewItem,
    SkribbySegmentEvidence,
    Task,
    Template,
    TrackerIssue,
)
from app.notes import UNKNOWN_SPEAKER, NotesFailed, generate_notes  # noqa: E402

REBUILT = "skribby-rebuilt"  # `meetings.transcript_source` (String(16)) after a rebuild
# Added to the prompt version of the notes call this script makes (`ai_runs.prompt_version`), so the
# cost report still sees an ordinary "notes" call, and a second rebuild does not mistake this one
# for the owner's own rewrite.
LEDGER_MARK = "+rebuild"
BACKUP_VERSION = 1
BACKUP_FOLDER = "rebuild-backups"
LOCK_WAIT = "5s"
LABEL = re.compile(r"(?m)^\[([^\]\n]{1,80})\]:[ \t]*")
KINDS = ("decisions", "open questions", "review items", "tasks")


class Refused(Exception):
    """A plain-words reason to stop. `kept` says what was left behind (a backup file)."""

    def __init__(self, message: str, kept: str = ""):
        super().__init__(message)
        self.kept = kept


@dataclass
class Plan:
    meeting: Meeting
    already: str | None = None  # why there is nothing to do, when there is none
    evidence: SkribbySegmentEvidence | None = None
    candidate: transcript_source.Candidate | None = None
    tags: dict = field(default_factory=dict)  # `_resolve_tags` read again now
    tag_segments: dict = field(default_factory=dict)
    segments: int = 0
    kept_until: datetime | None = None


# ---- reading ----


def _spoken(text_: str) -> str:
    return LABEL.sub("", text_ or "")


def _labels(text_: str | None) -> list[str]:
    return LABEL.findall(text_ or "")


def _body(items: list) -> dict:
    """The evidence items in the shape of Skribby's own answer, which is what `skribby._segments`
    and `skribby._resolve_tags` read (the evidence keeps the candidate names as plain strings)."""
    return {
        "transcript": [
            {
                "speaker": i.get("speaker"),
                "speaker_name": i.get("speaker_name"),
                "potential_speaker_names": [
                    {"name": name} for name in i.get("potential_speaker_names") or []
                ],
                "start": i.get("start"),
                "end": i.get("end"),
                "transcript": i.get("text"),
            }
            for i in items
        ]
    }


def _check_state(meeting: Meeting) -> None:
    if meeting.status != "ready":
        raise Refused(
            f"meeting {meeting.id} is not finished (its status is {meeting.status}): it is being "
            "recorded or processed, or it failed. Wait until it says ready."
        )
    if meeting.extraction_status == "running":
        raise Refused(
            f"the lists of meeting {meeting.id} (tasks, decisions) are being read right now. "
            "Wait a minute and run it again."
        )
    if meeting.bot_provider != "skribby":
        raise Refused(
            f"meeting {meeting.id} was run by the provider {meeting.bot_provider}, not skribby, "
            "so there is no Skribby evidence to rebuild from."
        )


def load(session: Session, meeting_id: int, now: datetime) -> Plan:
    """Everything a dry run needs, read-only. Raises `Refused` with the reason when the meeting
    cannot be rebuilt."""
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise Refused(f"there is no meeting {meeting_id}.")
    _check_state(meeting)
    if meeting.transcript_source == REBUILT:
        return Plan(
            meeting,
            already="already rebuilt from the saved evidence; nothing to do (to do it again, "
            "restore the backup first)",
        )
    row = session.scalars(
        select(SkribbySegmentEvidence).where(SkribbySegmentEvidence.meeting_id == meeting_id)
    ).first()
    if row is None:
        raise Refused(
            f"no saved evidence for meeting {meeting_id}. Skribby's segments are kept 7 days after "
            "the meeting and removed after that, or this meeting never had any."
        )
    kept_until = row.created_at + timedelta(days=pipeline.EVIDENCE_RETENTION_DAYS)
    if kept_until <= now:
        raise Refused(
            f"the evidence for meeting {meeting_id} has expired (kept until "
            f"{kept_until:%Y-%m-%d %H:%M} UTC)."
        )
    items = row.items
    if (
        not isinstance(items, list)
        or not items
        or not all(isinstance(item, dict) for item in items)
    ):
        raise Refused(f"the saved evidence for meeting {meeting_id} is unreadable.")
    by_elimination = get_settings().skribby_name_by_elimination
    # Every name the note taker goes by, as the live path excludes them: the meeting's stored name
    # (null = the server default) and the signed-in account's own name. Not the name Skribby
    # reports in its body: the evidence does not keep that one.
    own_names = bot_names.own_names(get_settings(), meeting.bot_name)
    body = _body(items)
    segments = skribby._segments(body, own_names, by_elimination)
    found = transcript_source.candidate(segments, skribby._last_segment_end(body))
    why_not = transcript_source.fallback_reason(
        found, meeting.recorded_seconds or 0, pipeline.MIN_WORDS
    )
    if why_not is not None:
        raise Refused(
            f"Skribby's saved text for meeting {meeting_id} cannot be used: it is {why_not} "
            "(the live path would have fallen back to our own transcription too)."
        )
    plan = Plan(
        meeting,
        evidence=row,
        candidate=found,
        tags=skribby._resolve_tags(body["transcript"], own_names, by_elimination),
        segments=len(items),
        kept_until=kept_until,
    )
    for item in items:
        tag = str(item.get("speaker")) if item.get("speaker") is not None else "(no tag)"
        plan.tag_segments[tag] = plan.tag_segments.get(tag, 0) + 1
    if meeting.transcript_source == transcript_source.SKRIBBY and _labels(
        meeting.speaker_transcript
    ) == _labels(found.text):
        plan.already = (
            "this meeting's transcript is already Skribby's own text with the same speakers; "
            "nothing to do"
        )
    return plan


def touched(session: Session, meeting_id: int) -> dict[str, set[str]]:
    """The fingerprints of what a person has done something to, by kind: a decision hidden, edited
    or given another status, or one that another decision is "replaced by"; an open question
    resolved or taken off the next agenda; a review item checked; an AI task they edited, moved,
    ticked or deleted (`tasks.touched`), or that was already pushed to Linear (its `tracker_issues`
    row goes with the task, and the replacement would never be pushed)."""
    found: dict[str, set[str]] = {kind: set() for kind in KINDS}
    decisions = list(session.scalars(select(Decision).where(Decision.meeting_id == meeting_id)))
    pointed_at = set(
        session.scalars(
            select(Decision.replaced_by_id).where(
                Decision.replaced_by_id.in_([row.id for row in decisions])
            )
        )
    )  # decisions, of any meeting, that say "replaced by" one of this meeting's
    for row in decisions:
        if (
            row.edited_at is not None
            or row.hidden_at is not None
            or row.status not in ("approved", "proposed")
            or row.id in pointed_at
        ):
            found["decisions"].add(row.fingerprint)
    for row in session.scalars(select(OpenQuestion).where(OpenQuestion.meeting_id == meeting_id)):
        if row.resolved_at is not None or not row.carry:
            found["open questions"].add(row.fingerprint)
    for row in session.scalars(select(ReviewItem).where(ReviewItem.meeting_id == meeting_id)):
        if row.checked_at is not None:
            found["review items"].add(row.fingerprint)
    for task in session.scalars(
        select(Task).where(Task.meeting_id == meeting_id, Task.source == "ai")
    ):
        if task.ai_fingerprint and (
            task_rules.touched(task)
            or task.deleted_at is not None
            or session.scalar(select(TrackerIssue.id).where(TrackerIssue.task_id == task.id))
            is not None
        ):
            found["tasks"].add(task.ai_fingerprint)
    return found


def existing(session: Session, meeting_id: int) -> dict[str, set[str]]:
    """The fingerprints of every list item the meeting has now (tasks: the AI ones not deleted)."""
    return {
        "decisions": set(
            session.scalars(select(Decision.fingerprint).where(Decision.meeting_id == meeting_id))
        ),
        "open questions": set(
            session.scalars(
                select(OpenQuestion.fingerprint).where(OpenQuestion.meeting_id == meeting_id)
            )
        ),
        "review items": set(
            session.scalars(
                select(ReviewItem.fingerprint).where(ReviewItem.meeting_id == meeting_id)
            )
        ),
        "tasks": set(
            session.scalars(
                select(Task.ai_fingerprint).where(
                    Task.meeting_id == meeting_id,
                    Task.source == "ai",
                    Task.deleted_at.is_(None),
                    Task.ai_fingerprint.is_not(None),
                )
            )
        ),
    }


def _wanted(parsed: ParsedNotes) -> dict[str, set[str]]:
    bullets = {
        "decisions": parsed.decisions,
        "open questions": parsed.open_questions,
        "review items": parsed.please_check,
        "tasks": parsed.action_items,
    }
    return {kind: {fingerprint(bullet) for bullet in bullets[kind]} for kind in KINDS}


def would_lose(found: dict[str, set[str]], parsed: ParsedNotes) -> dict[str, int]:
    """How many touched items of each kind the notes `parsed` would drop: their wording is not
    there, so the next read removes the row (or, for a task, makes a second one beside the kept
    one)."""
    wanted = _wanted(parsed)
    return {kind: len(found[kind] - wanted[kind]) for kind in KINDS}


def would_replace(
    have: dict[str, set[str]], found: dict[str, set[str]], parsed: ParsedNotes
) -> dict[str, int]:
    """How many UNTOUCHED items of each kind the notes `parsed` give a new row: their wording is
    gone, so the worker deletes the row and makes another (a new id: links in earlier mails and
    Slack posts to the old one stop working)."""
    wanted = _wanted(parsed)
    return {kind: len((have[kind] - found[kind]) - wanted[kind]) for kind in KINDS}


def _count_line(counts: dict[str, int]) -> str:
    return ", ".join(f"{counts[kind]} {kind}" for kind in KINDS)


def _rewrites(session: Session, meeting_id: int) -> int:
    """How many times the notes of this meeting were written successfully by the app itself: the
    pipeline's first writing, plus one for each time the owner rewrote them with a template. A
    writing by this script does not count."""
    return (
        session.scalar(
            select(func.count())
            .select_from(AiRun)
            .where(
                AiRun.meeting_id == meeting_id,
                AiRun.kind == "notes",
                AiRun.status == "ok",
                or_(AiRun.prompt_version.is_(None), ~AiRun.prompt_version.like(f"%{LEDGER_MARK}")),
            )
        )
        or 0
    )


def _rewritten_since(session: Session, meeting_id: int, since: datetime) -> int:
    """How many times the app wrote the notes of this meeting successfully after `since` (the
    owner's rewrite with a template). A writing by this script does not count."""
    return (
        session.scalar(
            select(func.count())
            .select_from(AiRun)
            .where(
                AiRun.meeting_id == meeting_id,
                AiRun.kind == "notes",
                AiRun.status == "ok",
                AiRun.created_at > since,
                or_(AiRun.prompt_version.is_(None), ~AiRun.prompt_version.like(f"%{LEDGER_MARK}")),
            )
        )
        or 0
    )


def _check_restorable(session: Session, meeting: Meeting, data: dict, force: bool) -> None:
    """Stops a restore that would overwrite newer work: the transcript is not the rebuilt one any
    more, or the owner rewrote the notes after the backup was taken (a rewrite leaves
    `transcript_source` as it was, and puts a new template on the meeting)."""
    if force:
        return
    if meeting.transcript_source != REBUILT:
        raise Refused(
            f"meeting {meeting.id} has changed since it was rebuilt (its transcript is not the "
            "rebuilt one any more), so putting the old text back would overwrite newer work. "
            "Run it again with --force if that is what is wanted."
        )
    try:
        saved = datetime.fromisoformat(str(data.get("saved_at")))
    except ValueError:
        raise Refused("the backup file has no readable time, so it cannot be checked.") from None
    if saved.tzinfo is None:
        saved = saved.replace(tzinfo=UTC)
    if _rewritten_since(session, meeting.id, saved):
        raise Refused(
            f"the owner rewrote the notes of meeting {meeting.id} after this backup was taken, so "
            "putting the old notes back would overwrite that rewrite. Run it again with --force "
            "if that is what is wanted."
        )


# ---- the dry run ----


def _how_counts(tags: dict) -> str:
    counts: dict[str, int] = {}
    for info in tags.values():
        counts[info.get("how", "?")] = counts.get(info.get("how", "?"), 0) + 1
    return ", ".join(f"{n} {how}" for how, n in sorted(counts.items())) or "none"


def describe(session: Session, plan: Plan) -> list[str]:
    meeting, found = plan.meeting, plan.candidate
    assert found is not None and plan.evidence is not None
    before_labels = _labels(meeting.speaker_transcript or meeting.transcript)
    named_before = {name for name in before_labels if name != UNKNOWN_SPEAKER}
    unnamed_before = sum(1 for name in before_labels if name == UNKNOWN_SPEAKER)
    unnamed_after = sum(1 for name in _labels(found.text) if name == UNKNOWN_SPEAKER)
    words_before = len(_spoken(meeting.transcript or "").split())
    change = f" ({(found.words - words_before) / words_before:+.0%})" if words_before else ""
    lines = [
        f"Meeting {meeting.id}: {meeting.status}, run by {meeting.bot_provider}, transcript now "
        f"from: {meeting.transcript_source or 'unknown'}",
        f"Evidence: saved {plan.evidence.created_at:%Y-%m-%d %H:%M} UTC, kept until "
        f"{plan.kept_until:%Y-%m-%d %H:%M} UTC; {plan.segments} segments, {len(plan.tags)} tags",
        "Tags read again now with the current naming rules (no names printed):",
    ]
    for tag in sorted(plan.tags):
        info = plan.tags[tag]
        named = "named" if info.get("name") else "NOT named"
        why = info.get("how", "?") + (f", {info['note']}" if info.get("note") else "")
        lines.append(f"  tag {tag}: {plan.tag_segments.get(tag, 0)} segments, {named} [{why}]")
    saved = plan.evidence.tags if isinstance(plan.evidence.tags, dict) else {}
    lines += [
        f"  saved with the evidence at the time: {_how_counts(saved)}",
        f"  read again now: {_how_counts(plan.tags)}",
        f"speakers named: before {len(named_before)}, after {found.speakers}",
        f"lines credited to no one: before {unnamed_before}, after {unnamed_after}",
        f"words: before {words_before}, after {found.words}{change}",
        f"speaker lines: {found.segments} segments become the new text; "
        f"speakers incomplete: before {'yes' if meeting.speakers_incomplete else 'no'}, "
        f"after {'no' if found.complete else 'yes'}",
    ]
    found_touched = touched(session, meeting.id)
    lines.append(
        "touched by a person: " + _count_line({kind: len(found_touched[kind]) for kind in KINDS})
    )
    lines.append(
        "  If the rewritten notes drop the wording of any of these, --apply stops and says so "
        "(use --force to go on and give them up)."
    )
    have = existing(session, meeting.id)
    untouched = sum(len(have[kind] - found_touched[kind]) for kind in KINDS)
    lines.append(
        f"untouched items now: {untouched}. A rewritten set of notes is worded differently, so "
        "most of them get a NEW row (a new id): a link in an earlier mail or Slack post to one "
        "stops working. --apply prints the exact number."
    )
    rewrites = _rewrites(session, meeting.id)
    if rewrites > 1:
        lines.append(
            f"  The notes were written {rewrites} times already (the owner rewrote them): --apply "
            "stops unless --force."
        )
    lines.append(
        "notes: will be rewritten from the new transcript with the meeting's own template "
        "(one model call). Tasks, decisions, open questions and review items are read again "
        "quietly by the worker afterwards: no New badge, no mail, no Slack or Linear post."
    )
    return lines


# ---- --apply ----


def _lock(session: Session, meeting_id: int) -> Meeting:
    """The meeting under a row lock, so the worker or an owner's rewrite cannot move it while the
    model is asked. Held until the single commit. A row someone holds for more than `LOCK_WAIT` is
    a plain refusal."""
    try:
        session.execute(text(f"set local lock_timeout = '{LOCK_WAIT}'"))
        meeting = session.scalars(
            select(Meeting)
            .where(Meeting.id == meeting_id)
            .with_for_update(key_share=True)  # FOR NO KEY UPDATE: a child insert still goes through
            .execution_options(populate_existing=True)
        ).one()
        session.execute(text("set local lock_timeout = 0"))  # the wait limit was for this lock only
        return meeting
    except OperationalError:
        session.rollback()
        raise Refused(
            f"meeting {meeting_id} is being worked on right now. Try again in a moment."
        ) from None


def save_backup(meeting: Meeting, folder: Path, now: datetime) -> Path:
    """The old text and notes, in a private file, written and read back before anything else
    changes. Raises `Refused` when it cannot be saved (nothing has changed then)."""
    data = {
        "version": BACKUP_VERSION,
        "meeting_id": meeting.id,
        "saved_at": now.isoformat(),
        "warning": "Private: this file holds what people said in a meeting. Never post it.",
        "transcript": meeting.transcript,
        "speaker_transcript": meeting.speaker_transcript,
        "speakers_incomplete": meeting.speakers_incomplete,
        "transcript_source": meeting.transcript_source,
        "notes": meeting.notes,
        "template_id": meeting.template_id,
    }
    path = folder / f"meeting-{meeting.id}-{now:%Y%m%dT%H%M%S%fZ}.json"
    try:
        made = not folder.exists()
        folder.mkdir(parents=True, exist_ok=True)
        if made:
            folder.chmod(0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        if json.loads(path.read_text(encoding="utf-8")) != data:
            raise OSError("the file read back differently")
        dir_fd = os.open(folder, os.O_RDONLY)  # the file's name must survive a crash too
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except (OSError, ValueError) as exc:
        raise Refused(
            f"the backup could not be saved ({type(exc).__name__}), so nothing was changed."
        ) from None
    return path


def rebuild(meeting_id: int, *, apply: bool, force: bool, folder: Path, client, out=print) -> int:
    now = datetime.now(UTC)
    with SessionLocal() as session:
        plan = load(session, meeting_id, now)
        if plan.already:
            out(f"Meeting {meeting_id}: {plan.already}.")
            return 0
        for line in describe(session, plan):
            out(line)
        if not apply:
            out("")
            out("DRY RUN: nothing was changed. Run it again with --apply to do it.")
            return 0
        session.rollback()  # the dry run's reads end here; the lock starts a new transaction
        return _apply(session, meeting_id, plan, force=force, folder=folder, client=client, out=out)


def _apply(session: Session, meeting_id: int, plan: Plan, *, force, folder, client, out) -> int:
    now = datetime.now(UTC)
    meeting = _lock(session, meeting_id)
    _check_state(meeting)
    if meeting.transcript_source == REBUILT:  # someone ran it between the look and the lock
        out(f"Meeting {meeting_id}: already rebuilt; nothing to do.")
        session.rollback()
        return 0
    template = session.get(Template, meeting.template_id)
    if template is None:
        raise Refused(f"meeting {meeting_id} has no notes template to write the notes with.")
    rewrites = _rewrites(session, meeting_id)
    if rewrites > 1 and not force:
        raise Refused(
            f"the notes of meeting {meeting_id} were written {rewrites} times: the owner rewrote "
            "them with a template, and this would replace that rewrite. Run it again with --force "
            "if that is what is wanted (the old notes are saved in the backup)."
        )
    path = save_backup(meeting, folder, now)
    kept = f"A backup of the old text was saved anyway at {path} (private)."
    found = plan.candidate
    assert found is not None
    try:
        # The same two steps as the live path, in the same order: the owner's vocabulary pass on
        # Skribby's text, then the notes from it.
        text_ = pipeline._apply_vocabulary(session, meeting, found.text, client)
        spent: list = []
        new_notes = generate_notes(
            text_,
            template,
            pipeline._terms(session, meeting.owner_user_id),
            client=client,
            usage=spent,
        )
    except NotesFailed:
        session.rollback()
        raise Refused(
            "the notes could not be written, so nothing was changed. The old notes were kept.",
            kept,
        ) from None
    found_touched = touched(session, meeting_id)
    parsed_new = parse_notes(new_notes)
    losing = would_lose(found_touched, parsed_new)
    replaced = would_replace(existing(session, meeting_id), found_touched, parsed_new)
    if sum(losing.values()) and not force:
        session.rollback()
        raise Refused(
            "the new notes do not contain the wording of items a person has touched: "
            f"{_count_line(losing)}. Rebuilding would remove them (or, for a task, add a second "
            "one beside it). Nothing was changed. Run it again with --force to go on anyway.",
            kept,
        )
    meeting.transcript = text_
    meeting.speaker_transcript = text_
    meeting.speakers_incomplete = not found.complete
    meeting.transcript_source = REBUILT
    meeting.notes = new_notes
    run = ai_runs.record_usage(
        session,
        meeting.id,
        "notes",
        get_settings().notes_model,
        spent,
        user_id=meeting.owner_user_id,
    )
    if run is not None and run.prompt_version:
        run.prompt_version = f"{run.prompt_version}{LEDGER_MARK}"[:32]
    workspace.mark_pending(meeting, quiet=True)
    session.commit()
    out("")
    out(f"APPLIED to meeting {meeting_id}: transcript, speaker transcript and notes rebuilt.")
    if sum(losing.values()):
        out(f"  --force gave up touched items the new notes do not contain: {_count_line(losing)}.")
    out(
        f"  Untouched items that get a NEW row because their wording changed: "
        f"{_count_line(replaced)}. A link in an earlier mail or Slack post to one of them stops "
        "working."
    )
    out(f"  Backup of the old text (PRIVATE, holds what people said; never post it): {path}")
    out(
        "  To put the old text back: python scripts/rebuild_meeting_from_evidence.py "
        f"--restore {path} --apply"
    )
    out(
        "  Tasks, decisions, open questions and review items are read again by the worker on its "
        "next pass, quietly (no New badge, no mail, no Slack or Linear post)."
    )
    return 0


# ---- --restore ----


def restore(path: Path, *, apply: bool, force: bool, out=print) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"{path} is not a readable backup file.") from None
    needed = ("meeting_id", "transcript", "speaker_transcript", "notes", "transcript_source")
    if (
        not isinstance(data, dict)
        or data.get("version") != BACKUP_VERSION
        or not all(key in data for key in needed)
        or not isinstance(data["meeting_id"], int)
    ):
        raise Refused(f"{path} is not a backup file this script wrote.")
    meeting_id = data["meeting_id"]
    with SessionLocal() as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None:
            raise Refused(f"there is no meeting {meeting_id} any more.")
        _check_state(meeting)
        _check_restorable(session, meeting, data, force)
        losing = would_lose(touched(session, meeting_id), parse_notes(data["notes"] or ""))
        out(
            f"Restore meeting {meeting_id} from the backup saved {data.get('saved_at', '?')}: "
            f"transcript, speaker transcript and notes go back to what they were; "
            f"transcript source back to {data['transcript_source'] or 'unknown'}."
        )
        if sum(losing.values()):
            out(f"  Touched items the old notes do not contain: {_count_line(losing)}.")
        if sum(losing.values()) and not force:
            raise Refused(
                "putting the old notes back would remove touched items (or add a second task "
                "beside them). Nothing was changed. Run it again with --force to go on anyway."
            )
        if not apply:
            out("")
            out("DRY RUN: nothing was changed. Run it again with --apply to do it.")
            return 0
        meeting = _lock(session, meeting_id)
        _check_state(meeting)
        _check_restorable(session, meeting, data, force)  # again, now that the row is held
        meeting.transcript = data["transcript"]
        meeting.speaker_transcript = data["speaker_transcript"]
        meeting.speakers_incomplete = bool(data.get("speakers_incomplete"))
        meeting.transcript_source = data["transcript_source"]
        meeting.notes = data["notes"]
        workspace.mark_pending(meeting, quiet=True)
        session.commit()
        out(f"RESTORED meeting {meeting_id}. The lists are read again by the worker, quietly.")
    return 0


def run(argv: list[str], client=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("meeting_id", nargs="?", type=int, help="the meeting to rebuild")
    parser.add_argument("--apply", action="store_true", help="make the change (default: a dry run)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="go on although the owner rewrote the notes, or a person's touched items would go",
    )
    parser.add_argument("--restore", metavar="FILE", help="put a backup's old text and notes back")
    parser.add_argument(
        "--backup-dir",
        metavar="DIR",
        help=f"where the backup goes (default: audio dir/{BACKUP_FOLDER})",
    )
    args = parser.parse_args(argv)
    if bool(args.restore) == (args.meeting_id is not None):
        parser.error("give a meeting id to rebuild, or --restore FILE, not both and not neither")
    try:
        if args.restore:
            return restore(Path(args.restore), apply=args.apply, force=args.force)
        folder = Path(args.backup_dir or Path(get_settings().audio_dir) / BACKUP_FOLDER)
        if client is None and args.apply:
            from app.notes import _client

            client = _client()
        return rebuild(
            args.meeting_id, apply=args.apply, force=args.force, folder=folder, client=client
        )
    except Refused as exc:
        print(f"REFUSED: {exc}")
        if exc.kept:
            print(exc.kept)
        else:
            print("Nothing was changed.")
        return 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
