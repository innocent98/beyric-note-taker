"""Asking for a draft, the queue, the worker and the daily cap (plan T5).

The request (`request_draft`) checks everything that needs no model, writes the draft as `writing`
with a `pending` ledger row (`ai_runs`, kind `recipe`) and answers; a worker thread claims it and
fills it (`process_next`, `run_loop`); the page polls. The pattern is the translation queue
(`app/translation.py`: `_claim`, `_finish`, `run_loop`) and the cap is Ask's pending-then-settle
ledger (`app/ask.py`):

* The cap (`recipe_daily_limit`, rolling 24 hours, across all recipes) counts ledger rows from the
  moment of the request. Every attempt that calls the model counts, failures and regenerations
  included; a request refused before any draft is written does not, and neither does a draft that
  fails before the model is called (`empty`, `too_long`, no readable meeting left): its pending row
  is deleted. Check and write are one short transaction under the account's advisory lock, so two
  requests at once cannot both take the last slot.
* Double-pressing Make, or two tabs: the same person asking for the same recipe, meetings and
  options while that draft is still `writing` gets that draft back (no second draft, no second
  count).
* The gate `make` (`feature_gates.require`) is checked here, before anything is written, in the
  function that leads to the model call. The worker is not gated: a queued draft was allowed.
* A claim is held `STALE_RUNNING`; a worker that died leaves a draft another claim picks up, at most
  `MAX_ATTEMPTS` times, then it fails with `gave_up`. The outcome is written with a compare-and-set
  on the claim, so a draft discarded or reclaimed meanwhile is not overwritten (its model call is
  still settled in the ledger: it cost).
* Nothing is held across the model call: the claim and the reading are committed first.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app import ai_runs, entitlements, feature_gates, team_admin, teams
from app.ai_versions import RECIPE_PROMPT_VERSION
from app.ask import transcript_of
from app.config import get_settings
from app.models import AiRun, Document, DocumentMeeting, Meeting, Recipe, TeamBrief, User
from app.recipes import blocks as blocks_mod
from app.recipes import generate
from app.recipes.generate import GenerationFailed, RecipeSpec
from app.services import Refused
from app.visibility import visible_meetings

logger = logging.getLogger(__name__)

MAX_MEETINGS = 12
MAX_ATTEMPTS = 3  # claims of one draft, counting those a dead worker never finished
STALE_RUNNING = timedelta(minutes=10)  # one model call of up to 3 minutes, with room
DOUBLE_SUBMIT_WINDOW = timedelta(minutes=2)
# A draft that fails with one of these never reached the model: it is not counted.
NOT_CALLED = ("empty", "too_long", "not_ready", "recipe_gone", "owner_gone", "access_gone")


# ---- the recipe, from a starter (code constant) or a row ----


@dataclass
class Resolved:
    spec: RecipeSpec
    scope: str
    uses_brief: bool
    recipe_id: int | None
    key: str | None


def starter_for(key: str):
    """A starter recipe by key, from `app/recipes/library.py` (slice S3 writes it: `STARTERS`, a
    list or a dict of objects with `key`, `name`, `instruction`, `output_type`, `scope` and
    `uses_brief`). None when there is no such starter (or no library yet)."""
    try:
        from app.recipes import library
    except ImportError:
        return None
    found = getattr(library, "STARTERS", ())
    items = found.values() if isinstance(found, dict) else found
    return next((s for s in items if s.key == key), None)


def _not_found(what: str) -> Refused:
    return Refused(404, "not_found", f"No such {what}.")


def resolve_recipe(session: Session, user: User, ref: int | str, team_id: int) -> Resolved:
    """The recipe `ref` names: a starter key, or the id of a recipe the person may use (their own,
    or their active team's). 404 for anything else, as for any recipe that is not theirs."""
    if isinstance(ref, str):
        starter = starter_for(ref)
        if starter is None:
            raise _not_found("recipe")
        return Resolved(
            generate.spec_of(starter),
            getattr(starter, "scope", "one"),
            getattr(starter, "uses_brief", True),
            None,
            starter.key,
        )
    row = _usable_recipe(session, user, ref, team_id)
    if row is None:
        raise _not_found("recipe")
    return Resolved(generate.spec_of(row), row.scope, row.uses_brief, row.id, row.key)


def _usable_recipe(session: Session, user: User, recipe_id: int, team_id: int) -> Recipe | None:
    """The recipe row, if it is not archived and is the person's own or their team's."""
    return session.scalars(
        select(Recipe).where(
            Recipe.id == recipe_id,
            Recipe.archived_at.is_(None),
            (Recipe.owner_user_id == user.id) & Recipe.team_id.is_(None)
            | (Recipe.team_id == team_id),
        )
    ).first()


# ---- the daily cap ----


def _lock_user(session: Session, user_id: int) -> None:
    """Serialise one account's requests, so two at once cannot both pass the cap or both make a
    draft."""
    session.execute(select(func.pg_advisory_xact_lock(0x7263_0000_0000_0000 | user_id)))


def daily_count(session: Session, user_id: int, now: datetime) -> int:
    """Drafts the account asked for in the last 24 hours that count: a ledger row each, `pending`
    and failed ones included (they cost or may have cost)."""
    return (
        session.scalar(
            select(func.count())
            .select_from(AiRun)
            .where(
                AiRun.kind == "recipe",
                AiRun.user_id == user_id,
                AiRun.created_at > now - timedelta(hours=24),
            )
        )
        or 0
    )


# ---- the request ----


def _meeting_set(session: Session, document_id: int) -> set[int]:
    return set(
        session.scalars(
            select(DocumentMeeting.meeting_id).where(DocumentMeeting.document_id == document_id)
        )
    )


def _options(options: dict | None) -> dict:
    import json

    return json.loads(generate._options_text(options))


def _readable_meetings(
    session: Session, user: User, ids: list[int], scope: str
) -> tuple[list[Meeting], int]:
    """The meetings the draft is made from and how many chosen ones were left out. A recipe on one
    meeting needs that meeting to be readable and ready (404, 409); a cross-meeting one leaves out
    what the caller cannot read or what is not ready, and needs at least one left."""
    if scope == "one" and len(ids) != 1:
        raise Refused(422, "meetings_invalid", "Choose one meeting.")
    if not ids or len(ids) > MAX_MEETINGS:
        raise Refused(422, "meetings_invalid", f"Choose between 1 and {MAX_MEETINGS} meetings.")
    found = {m.id: m for m in session.scalars(visible_meetings(user).where(Meeting.id.in_(ids)))}
    ordered = [found[i] for i in ids if i in found]
    if not ordered:
        raise _not_found("meeting")
    ready = [
        m
        for m in ordered
        if m.status == "ready" and ((m.notes or "").strip() or transcript_of(m).strip())
    ]
    if scope == "one" and not ready:
        raise Refused(409, "not_ready", "The notes are not ready yet.")
    if not ready:
        raise Refused(409, "not_ready", "None of those meetings is ready yet.")
    return ready, len(ids) - len(ready)


def request_draft(
    session: Session,
    user: User,
    *,
    recipe_ref: int | str,
    meeting_ids: list[int],
    options: dict | None = None,
    team_id: int | None = None,
    use_brief: bool = True,
    now: datetime | None = None,
) -> Document:
    """Queue a draft, or hand back the one this person just asked for. Commits. Raises `Refused`:
    404 (no such recipe, team or meeting, including a meeting the caller cannot read), 422
    `meetings_invalid`, 409 `not_ready`, 403 `plan_inactive` (the `make` gate), 429
    `recipe_limit`."""
    now = now or datetime.now(UTC)
    member = team_admin.my_membership(session, user, team_id)
    resolved = resolve_recipe(session, user, recipe_ref, member.team_id)
    ids = list(dict.fromkeys(meeting_ids))
    meetings, left_out = _readable_meetings(session, user, ids, resolved.scope)
    chosen = {m.id for m in meetings}
    clean = _options(options)
    feature_gates.require(session, user, entitlements.MAKE, now=now)
    settings = get_settings()
    _lock_user(session, user.id)
    for existing in session.scalars(
        select(Document).where(
            Document.owner_user_id == user.id,
            Document.status == "writing",
            Document.created_at >= now - DOUBLE_SUBMIT_WINDOW,
            Document.recipe_id.is_(resolved.recipe_id)
            if resolved.recipe_id is None
            else Document.recipe_id == resolved.recipe_id,
            Document.recipe_key.is_(resolved.key)
            if resolved.key is None
            else Document.recipe_key == resolved.key,
        )
    ):
        if existing.options == clean and _meeting_set(session, existing.id) == chosen:
            session.commit()  # releases the lock
            return existing
    if daily_count(session, user.id, now) >= settings.recipe_daily_limit:
        session.rollback()
        raise Refused(
            429,
            "recipe_limit",
            f"You have made {settings.recipe_daily_limit} documents today. Try again tomorrow.",
        )
    model = settings.recipe_model
    run = ai_runs.record(
        session,
        meetings[0].id if len(meetings) == 1 else None,
        "recipe",
        model,
        status="pending",
        prompt_version=RECIPE_PROMPT_VERSION,
        user_id=user.id,
    )
    run.created_at = now  # the cap counts from this stamp
    document = Document(
        owner_user_id=user.id,
        team_id=member.team_id,
        recipe_id=resolved.recipe_id,
        recipe_key=resolved.key,
        title=resolved.spec.name[:200],
        output_type=resolved.spec.output_type,
        status="writing",
        blocks=[],
        options=clean,
        use_brief=bool(use_brief and resolved.uses_brief),
        left_out_count=left_out,
        ledger_run_id=run.id,
        created_at=now,
        updated_at=now,
    )
    session.add(document)
    session.flush()
    session.add_all(DocumentMeeting(document_id=document.id, meeting_id=m.id) for m in meetings)
    session.commit()
    return document


# ---- the worker's side ----


@dataclass
class _Job:
    id: int
    run_id: int | None
    stamp: datetime
    spec: RecipeSpec
    options: dict
    context: generate.Context


def _claim(session: Session, now: datetime) -> Document | None:
    """Lock the next draft that is queued, or running since long enough that its worker must have
    died, and mark it started. One that has been tried too often is failed instead."""
    while True:
        row = session.scalars(
            select(Document)
            .join(User, User.id == Document.owner_user_id)
            .where(
                Document.status == "writing",
                (Document.started_at.is_(None)) | (Document.started_at <= now - STALE_RUNNING),
                User.status != "deleted",
            )
            .order_by(Document.id)
            .limit(1)
            .with_for_update(skip_locked=True, of=Document)
        ).first()
        if row is None:
            return None
        if row.attempts >= MAX_ATTEMPTS:
            row.status, row.failure_code, row.updated_at = "failed", "gave_up", now
            _settle(session, row.ledger_run_id, ok=False, error=RuntimeError("gave_up"))
            session.commit()
            continue
        row.started_at, row.attempts = now, row.attempts + 1
        return row


def _settle(session, run_id, *, ok, error=None, tokens=(None, None), called=True) -> None:
    """The pending ledger row of a draft: its outcome, or gone when no model call was made."""
    if run_id is None:
        return
    if not called:
        session.execute(delete(AiRun).where(AiRun.id == run_id, AiRun.status == "pending"))
        return
    ai_runs.settle(
        session,
        run_id,
        status="ok" if ok else "failed",
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        error=error,
    )


def _fail_before_model(session: Session, row: Document, now: datetime, code: str) -> None:
    row.status, row.failure_code, row.updated_at = "failed", code, now
    _settle(session, row.ledger_run_id, ok=False, called=False)
    row.ledger_run_id = None


def _read_job(session: Session, row: Document, now: datetime) -> _Job | None:
    owner = session.get(User, row.owner_user_id)
    if owner is None or owner.status == "deleted":
        _fail_before_model(session, row, now, "owner_gone")
        return None
    # Access is checked again now, as at the request: the person may have left the team (its brief
    # and its recipes are no longer theirs) or the recipe may have been archived since.
    if row.team_id is not None and teams.membership(session, owner.id, row.team_id) is None:
        _fail_before_model(session, row, now, "access_gone")
        return None
    resolved = None
    if row.recipe_id is not None:
        recipe = _usable_recipe(session, owner, row.recipe_id, row.team_id)
        if recipe is not None:
            resolved = Resolved(
                generate.spec_of(recipe), recipe.scope, recipe.uses_brief, recipe.id, recipe.key
            )
    elif row.recipe_key:
        starter = starter_for(row.recipe_key)
        if starter is not None:
            resolved = Resolved(
                generate.spec_of(starter),
                getattr(starter, "scope", "one"),
                True,
                None,
                starter.key,
            )
    if resolved is None:
        _fail_before_model(session, row, now, "recipe_gone")
        return None
    ids = _meeting_set(session, row.id)
    # Read again now, as the owner: a share may have been withdrawn since the request.
    meetings = list(
        session.scalars(
            visible_meetings(owner)
            .where(Meeting.id.in_(ids), Meeting.status == "ready")
            .order_by(Meeting.id)
        )
    )
    lost = len(ids) - len(meetings)
    if lost > 0:
        row.left_out_count += lost
    if not meetings:
        _fail_before_model(session, row, now, "not_ready")
        return None
    brief = None
    if row.use_brief and row.team_id is not None:
        brief = session.scalar(select(TeamBrief.text).where(TeamBrief.team_id == row.team_id))
    try:
        context = generate.build_context(
            session,
            owner,
            meetings,
            whole_transcript=resolved.scope == "one" and len(meetings) == 1,
            brief=brief,
        )
    except GenerationFailed as exc:
        _fail_before_model(session, row, now, exc.code)
        return None
    return _Job(row.id, row.ledger_run_id, now, resolved.spec, dict(row.options), context)


def _finish(session: Session, job: _Job, now: datetime, **values) -> bool:
    """Write the outcome if the draft is still this claim (not discarded, not reclaimed). Returns
    whether it won."""
    won = session.execute(
        update(Document)
        .where(
            Document.id == job.id,
            Document.status == "writing",
            Document.started_at == job.stamp,
        )
        .values(updated_at=now, **values)
    ).rowcount
    return bool(won)


def process_next(session: Session, now: datetime | None = None, *, client=None) -> int | None:
    """One worker step: write the next due draft. Returns the draft's id, or None when none was
    due. Whatever goes wrong fails that draft, never the caller."""
    now = now or datetime.now(UTC)
    row = _claim(session, now)
    if row is None:
        session.rollback()
        return None
    document_id = row.id
    job = _read_job(session, row, now)
    session.commit()
    if job is None:
        return document_id
    settings = get_settings()
    model = settings.recipe_model
    tokens: tuple[int | None, int | None] = (None, None)
    error: BaseException | None = None
    called = True
    try:
        result = generate.generate_blocks(
            job.spec, job.context, job.options, client=client, max_blocks=settings.draft_max_blocks
        )
        tokens = (result.input_tokens, result.output_tokens)
        won = _finish(
            session,
            job,
            now,
            status="ready",
            title=result.title[:200],
            blocks=blocks_mod.dump_blocks(result.blocks),
            source_hash=job.context.source_hash,
            model=model,
            prompt_version=RECIPE_PROMPT_VERSION,
            failure_code=None,
            ledger_run_id=None,
        )
    except GenerationFailed as exc:
        error, tokens = exc, (exc.input_tokens, exc.output_tokens)
        called = exc.code not in NOT_CALLED
        won = _finish(
            session,
            job,
            now,
            status="failed",
            failure_code=exc.code,
            model=model,
            ledger_run_id=None,
        )
    except Exception as exc:  # whatever else must not stop the worker
        session.rollback()
        logger.warning("draft %s: unexpected %s", document_id, type(exc).__name__)
        error = exc
        won = _finish(
            session,
            job,
            now,
            status="failed",
            failure_code="error",
            model=model,
            ledger_run_id=None,
        )
    # The model call is settled whatever happened to the draft meanwhile: it cost.
    _settle(session, job.run_id, ok=error is None, error=error, tokens=tokens, called=called)
    session.commit()
    if not won:
        logger.info(
            "draft %s was changed while it was being written; the answer is dropped", document_id
        )
    return document_id


def run_loop(stop: threading.Event, *, session_factory=None, idle_seconds: float = 2.0) -> None:
    """The worker's draft thread: one draft at a time until `stop` is set. A failing pass (the
    database briefly down) is logged by type and the loop goes on."""
    if session_factory is None:
        from app.db import SessionLocal

        session_factory = SessionLocal
    while not stop.is_set():
        found = None
        try:
            with session_factory() as session:
                found = process_next(session, datetime.now(UTC))
        except Exception as exc:
            logger.warning("recipe pass failed (%s)", type(exc).__name__)
        if found is None:
            stop.wait(idle_seconds)


# ---- reading a draft (the API's side) ----

FAILURE_MESSAGES = {
    "empty": "There was nothing in the meeting to write from.",
    "too_long": "That meeting is too long to write from in one go. Try a shorter one.",
    "model_error": "The writing service did not answer. Try again in a minute.",
    "model_reply": "The writing service sent back something unusable. Try again.",
    "structure": "The writing service sent back something unusable. Try again.",
    "gave_up": "This took too long and was stopped. Try again.",
    "not_ready": "The meeting is no longer available to write from.",
    "recipe_gone": "That recipe no longer exists.",
    "owner_gone": "This draft can no longer be written.",
    "access_gone": "You no longer have access to what this draft was to be made from.",
    "error": "Something went wrong. Try again.",
}


def failure_message(code: str | None) -> str | None:
    """A plain sentence for a draft's failure code; null for a draft that did not fail."""
    return None if code is None else FAILURE_MESSAGES.get(code, FAILURE_MESSAGES["error"])


@dataclass
class Reading:
    """What the screen shows besides the blocks: the audit of the CURRENT blocks, whether the
    meetings' text moved on, and whether any source meeting can still be opened."""

    blanks: list
    flags: list
    stale: bool
    sources_available: bool


def read_meetings(session: Session, user: User, document: Document) -> list[Meeting]:
    """The meetings of the draft that still exist and that `user` may read."""
    ids = _meeting_set(session, document.id)
    if not ids:
        return []
    return list(
        session.scalars(visible_meetings(user).where(Meeting.id.in_(ids)).order_by(Meeting.id))
    )


def reading(session: Session, user: User, document: Document) -> Reading:
    """Audit the draft as it stands now (after a person's edits too) against the meetings' text
    and the brief, as the draft's owner would see it. Nothing is stored."""
    from app.recipes import audit

    meetings = read_meetings(session, user, document)
    if document.status != "ready":
        return Reading([], [], False, bool(meetings))
    try:
        blocks = blocks_mod.load_blocks(document.blocks)
    except blocks_mod.BlocksInvalid:
        blocks = []
    brief = None
    if document.use_brief and document.team_id is not None:
        brief = session.scalar(select(TeamBrief.text).where(TeamBrief.team_id == document.team_id))
    corpus: list[str] = list(filter(None, [brief]))
    if meetings:
        try:
            corpus = generate.build_context(
                session, user, meetings, whole_transcript=len(meetings) == 1, brief=brief
            ).corpus
        except GenerationFailed:
            pass
    stale = bool(
        document.source_hash
        and meetings
        and generate.source_hash(meetings, brief) != document.source_hash
    )
    return Reading(audit.blanks(blocks), audit.audit(blocks, corpus), stale, bool(meetings))
