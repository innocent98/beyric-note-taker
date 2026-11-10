"""Retry policy shared by every OpenAI call: rate limits, timeouts, connection drops and 5xx."""

import time
from collections.abc import Callable

import openai

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 3.0)
_RETRYABLE = (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)


def retryable(exc: Exception) -> bool:
    if isinstance(exc, _RETRYABLE):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code >= 500


def call_with_retry[T](
    call: Callable[[], T], *, what: str, failure: type[Exception], attempts: int | None = None
) -> T:
    """Run `call`, retrying transient OpenAI errors. Raise `failure` when attempts run out.

    `attempts` defaults to MAX_ATTEMPTS; a caller that pays per try (a diagnostic script) can ask
    for fewer, down to 1 (no retry).
    """
    attempts = MAX_ATTEMPTS if attempts is None else attempts
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except openai.OpenAIError as exc:
            if not retryable(exc) or attempt == attempts:
                raise failure(
                    f"{what}: {type(exc).__name__} after {attempt} attempt(s): {exc}"
                ) from exc
            time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS)) - 1])
    raise AssertionError("unreachable")
