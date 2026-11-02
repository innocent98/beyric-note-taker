from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from app import retry
from app.transcribe import (
    MAX_KEYWORD_CHARS,
    MAX_KEYWORDS,
    TranscriptionFailed,
    transcribe_chunks,
)


class FakeOpenAI:
    """Stands in for OpenAI(); `script` holds one result or exception per call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "filename": Path(kwargs["file"].name).name})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item)


def status_error(cls, code):
    request = httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")
    return cls("boom", response=httpx.Response(code, request=request), body=None)


@pytest.fixture
def fake_openai(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", sleeps.append)

    def make(script):
        client = FakeOpenAI(script)
        client.sleeps = sleeps
        return client

    return make


@pytest.fixture
def chunks(tmp_path):
    paths = []
    for name in ("chunk_000.webm", "chunk_001.webm"):
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(path)
    return paths


def test_joins_chunks_in_order(fake_openai, chunks):
    client = fake_openai(["first part", "second part"])
    text = transcribe_chunks(chunks, ["Beyric"], client=client)
    assert text == "first part\nsecond part"
    assert [c["filename"] for c in client.calls] == ["chunk_000.webm", "chunk_001.webm"]
    assert all(c["model"] == "gpt-transcribe" for c in client.calls)


def test_sends_terms_as_a_keywords_list(fake_openai, chunks):
    client = fake_openai(["a", "b"])
    transcribe_chunks(chunks, ["Beyric", "Vexa", "Beyric"], client=client)
    assert client.calls[0]["keywords"] == ["Beyric", "Vexa"]
    assert client.calls[1]["keywords"] == ["Beyric", "Vexa"]


def test_keywords_are_capped_in_count_and_length(fake_openai, chunks):
    client = fake_openai(["a", "b"])
    terms = [f"term{i}" for i in range(MAX_KEYWORDS + 20)]
    terms[0] = "x" * (MAX_KEYWORD_CHARS + 50)
    transcribe_chunks(chunks, terms, client=client)
    sent = client.calls[0]["keywords"]
    assert len(sent) == MAX_KEYWORDS
    assert sent[0] == "x" * MAX_KEYWORD_CHARS
    assert sent[1] == "term1"


def test_no_terms_sends_no_keywords(fake_openai, chunks):
    client = fake_openai(["a", "b"])
    transcribe_chunks(chunks, ["", "  "], client=client)
    assert "keywords" not in client.calls[0]


def test_retries_then_succeeds(fake_openai, chunks):
    client = fake_openai(
        [
            status_error(openai.RateLimitError, 429),
            openai.APIConnectionError(request=httpx.Request("POST", "https://x")),
            "ok",
            "next",
        ]
    )
    assert transcribe_chunks(chunks, [], client=client) == "ok\nnext"
    assert len(client.calls) == 4
    assert len(client.sleeps) == 2


def test_retries_on_server_error_and_timeout(fake_openai, chunks):
    client = fake_openai(
        [
            status_error(openai.InternalServerError, 503),
            openai.APITimeoutError(request=httpx.Request("POST", "https://x")),
            "ok",
            "next",
        ]
    )
    assert transcribe_chunks(chunks, [], client=client) == "ok\nnext"


def test_gives_up_after_three_attempts(fake_openai, chunks):
    client = fake_openai([status_error(openai.RateLimitError, 429)] * 3)
    with pytest.raises(TranscriptionFailed):
        transcribe_chunks(chunks, [], client=client)
    assert len(client.calls) == 3
    assert len(client.sleeps) == 2


def test_permanent_client_error_fails_immediately(fake_openai, chunks):
    client = fake_openai([status_error(openai.BadRequestError, 400)])
    with pytest.raises(TranscriptionFailed):
        transcribe_chunks(chunks, [], client=client)
    assert len(client.calls) == 1
    assert client.sleeps == []


def test_invalid_term_is_removed_from_keywords(fake_openai, chunks):
    client = fake_openai(["a", "b"])
    terms = ["Beyric", "<script>", "a>b", "line\nbreak", "cr\rterm", "Vexa"]
    transcribe_chunks(chunks, terms, client=client)
    assert client.calls[0]["keywords"] == ["Beyric", "Vexa"]


def test_no_chunks_returns_empty_string(fake_openai):
    assert transcribe_chunks([], ["Beyric"], client=fake_openai([])) == ""
