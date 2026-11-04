"""The ledger of model calls for the AI workspace (`ai_runs`), and the prompt versions.

A prompt version names the text a call was made with, so a change in quality or cost can be tied to
a change of prompt. Bump the constant whenever the system prompt or the output schema changes.
Nothing re-extracts old meetings by itself when it changes: put the ones you want redone back to
`extraction_status = 'pending'` (`workspace.mark_pending`).
"""

from sqlalchemy.orm import Session

from app.ai_versions import (
    EXTRACT_PROMPT_VERSION,
    EXTRACT_REVIEW_PROMPT_VERSION,
    LABEL_PROMPT_VERSION,
    NOTES_PROMPT_VERSION,
    TRANSLATE_PROMPT_VERSION,
)
from app.models import AiRun

__all__ = [
    "EXTRACT_PROMPT_VERSION",
    "EXTRACT_REVIEW_PROMPT_VERSION",
    "LABEL_PROMPT_VERSION",
    "NOTES_PROMPT_VERSION",
    "TRANSLATE_PROMPT_VERSION",
    "record",
]

_VERSION_OF_KIND = {"extract": EXTRACT_PROMPT_VERSION, "translate": TRANSLATE_PROMPT_VERSION}


def record(
    session: Session,
    meeting_id: int | None,
    kind: str,
    model: str,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    status: str = "ok",
    error: BaseException | None = None,
    prompt_version: str | None = None,
    user_id: int | None = None,
) -> AiRun:
    """Add one call to the ledger (the caller commits). `error` is stored as a class name only:
    the underlying one when the exception wraps another (an `ExtractionFailed` over a
    `RateLimitError` reads `RateLimitError`). `prompt_version` defaults to the kind's usual
    one. `user_id` is set where a per-account count is needed (translations)."""
    run = AiRun(
        meeting_id=meeting_id,
        user_id=user_id,
        kind=kind,
        model=model,
        prompt_version=prompt_version or _VERSION_OF_KIND.get(kind),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        status=status,
        error=type(error.__cause__ or error).__name__ if error is not None else None,
    )
    session.add(run)
    session.flush()
    return run
