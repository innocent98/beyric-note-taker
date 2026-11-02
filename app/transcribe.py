"""Transcribe prepared audio chunks with OpenAI, biased toward the user's own vocabulary.

Live check (2026-11-02, gpt-transcribe): "Beyric" came back as "BIRIC" with no help and as
"Biric" with `prompt`, but correctly with `keywords`. So vocabulary goes in `keywords`.
"""

from pathlib import Path

from openai import OpenAI

from app.config import get_settings
from app.retry import call_with_retry

REQUEST_TIMEOUT = 300.0
_INVALID_KEYWORD_CHARS = ("<", ">", "\r", "\n")


class TranscriptionFailed(Exception):
    """A chunk could not be transcribed."""


def _keywords(terms: list[str]) -> str:
    """One term per line. The API rejects the whole request over a bad character, so drop them."""
    seen: dict[str, None] = {}
    for term in terms:
        term = term.strip()
        if term and not any(char in term for char in _INVALID_KEYWORD_CHARS):
            seen.setdefault(term)
    return "\n".join(seen)


def _transcribe_one(client, chunk: Path, model: str, keywords: str) -> str:
    def call() -> str:
        with chunk.open("rb") as audio:
            extra = {"keywords": keywords} if keywords else {}
            return client.audio.transcriptions.create(model=model, file=audio, **extra).text

    return call_with_retry(call, what=chunk.name, failure=TranscriptionFailed)


def transcribe_chunks(chunks: list[Path], terms: list[str], *, client=None) -> str:
    """Transcribe `chunks` in order and join them. `terms` are names and jargon to spell right."""
    if not chunks:
        return ""
    settings = get_settings()
    if client is None:
        client = OpenAI(api_key=settings.openai_api_key, max_retries=0, timeout=REQUEST_TIMEOUT)
    keywords = _keywords(terms)
    parts = [
        _transcribe_one(client, Path(c), settings.transcription_model, keywords) for c in chunks
    ]
    return "\n".join(part.strip() for part in parts)
