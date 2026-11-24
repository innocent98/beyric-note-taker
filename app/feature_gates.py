"""The feature gates and their mode switch (billing spec 2.3, stage S3).

Ask Beyric, a NEW translation and rewriting notes with a template each call the model on every
use, so each needs a person in a state that may use it (`entitlements.OPEN_STATES`). The gate is
checked against the CALLER, the person pressing the button, not the meeting's recorder. It sits
inside the function that makes the model call, never only in a route, so every way in is covered:

- `ask`: `ask.answer`, reached by `POST /api/ask`.
- `translate`: `translation.request_translation`, after the cache check. Reached by
  `POST /api/meetings/{id}/translations` and by `shares.create_share` (notes sent in French or
  Spanish).
- `rewrite`: `services.regenerate_notes`, before the model call. Reached by
  `POST /api/meetings/{id}/regenerate` and by the legacy page route.
- `make`: `recipes.service.request_draft`, before anything is written or counted. Reached by
  `POST /api/documents`. The worker is not gated: a queued draft was allowed when it was asked for.

Not gated, on purpose (spec 2.3): reading, search, export, tasks, decisions, an existing
translation, a retry of a failed meeting, and Review's "add the corrected spelling" (no model).
The translation worker is not gated either: a queued translation was allowed when it was asked for.

`FEATURE_GATES_MODE`: `off` (default) returns at once, with no query. `shadow` lets the call go on
and logs one line when the gate would have refused (feature, user id, state; no names or emails);
a failure of the check is logged and never stops the call. `enforce` refuses with 403
`plan_inactive` and fails closed. It ships dark and is switched on only at go-live (S11): the
pilot accounts have these features today.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app import entitlements
from app.config import Settings, get_settings
from app.models import User

logger = logging.getLogger(__name__)

PLAN_INACTIVE = "plan_inactive"
_WORDS = {
    entitlements.ASK: "Asking Beyric",
    entitlements.TRANSLATE: "Translating notes",
    entitlements.REWRITE: "Rewriting notes",
    entitlements.MAKE: "Making documents",
}


def plan_inactive_message(feature: str) -> str:
    """What a person reads when a gate refuses them: no word about a pilot, no address, and what
    stays open."""
    return (
        f"{_WORDS[feature]} needs an active plan or trial. "
        "You can still read, search and export your notes, and use your tasks and decisions."
    )


def require(
    session: Session,
    user: User,
    feature: str,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> None:
    """Let the call go on, or raise `Refused` 403 `plan_inactive` (enforce only). Returns at once
    under `off`. Reads only: the caller keeps whatever transaction or lock it holds (a caller
    holding a row lock rolls back when this raises)."""
    settings = settings or get_settings()
    mode = settings.feature_gates_mode
    if mode == "off":
        return
    now = now or datetime.now(UTC)
    max_poll = settings.max_poll_hours * 3600
    if mode == "shadow":
        try:
            with session.begin_nested():
                decision = entitlements.gate(session, user, feature, now, max_poll)
        except Exception as exc:
            # The class and the user id only: a database error's text carries bound parameters,
            # and one of them is the email fingerprint.
            logger.error(
                "feature_gates_shadow failed feature=%s user=%s error=%s",
                feature,
                user.id,
                type(exc).__name__,
            )
            return
        if not decision.allowed:
            logger.info(
                "feature_gates_shadow would have refused feature=%s user=%s state=%s",
                feature,
                user.id,
                decision.state,
            )
        return
    if not entitlements.gate(session, user, feature, now, max_poll).allowed:
        from app.services import Refused  # services imports this module: a top import would loop

        raise Refused(403, PLAN_INACTIVE, plan_inactive_message(feature))
