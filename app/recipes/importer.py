"""Read a file into brief text (plan T11): `.docx`, `.pdf`, `.txt` and `.md`.

The text goes back to the person to review and edit; nothing is saved here and the file is never
stored: not on disk, not in the database (the route reads the upload in memory). Imported text is
DATA, like the brief it may become: it is stored only if the person saves it, and the model sees it
inside a fence (`app/recipes/generate.py`), never as instructions.

Word and PDF files are opened in a child process (`app/recipes/import_child.py`) with a hard time
limit: a file made to hang is killed, and the memory it needed goes back to the system. At most
`MAX_AT_ONCE` run together in one web process. Plain text needs no parser, so it is read here and
never waits.

Refusals are `ImportRefused(code)`: `too_big` (the file, or a Word file that unpacks to too much),
`too_many_pages`, `unsupported`, `no_text_layer` (nothing to read: a scan, or an empty file),
`unreadable` (damaged, locked, or too slow to read) and `busy`.
"""

import json
import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.recipes.import_child import MAX_ZIP_MEMBERS, clean_text  # noqa: F401  (MAX_ZIP_MEMBERS: tests)

logger = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parent.parent.parent
# Below nginx's `proxy_read_timeout` (60 s, deploy/nginx.conf) so a slow file answers with our own
# JSON error and not the edge's gateway page.
TIME_LIMIT_SECONDS = 20
MAX_AT_ONCE = 2
KINDS = {".docx": "docx", ".pdf": "pdf", ".txt": "text", ".md": "text"}

MESSAGES = {
    "too_big": "That file is too big. Try a smaller one, or paste the text in.",
    "too_many_pages": "That PDF has too many pages. Paste the part you need instead.",
    "unsupported": "Beyric reads .docx, .pdf, .txt and .md files. Save the file in one of these.",
    "no_text_layer": "There is no text we can read in that file. A scanned PDF has none: paste "
    "the text in instead.",
    "unreadable": "We could not read that file. It may be damaged or locked with a password.",
    "busy": "Other files are being read right now. Try again in a moment.",
}

_slots = threading.Semaphore(MAX_AT_ONCE)


class ImportRefused(Exception):
    """`code` is a short word and `message` is safe to show: neither carries the file's words."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or MESSAGES[code])
        self.code = code


@dataclass
class ExtractedText:
    text: str
    truncated: bool  # the file held more than a brief may (`brief_max_chars`): the rest is cut
    pages: int | None  # a PDF's page count, else None


def _child_env() -> dict[str, str]:
    """Only PATH: none of the app's settings or secrets reach the parser."""
    return {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}


def _decode(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        raise ImportRefused("unreadable")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _kind(filename: str) -> str:
    suffix = Path(filename.replace("\\", "/")).suffix.lower()
    kind = KINDS.get(suffix)
    if kind is None or Path(filename.replace("\\", "/")).stem == "":
        raise ImportRefused("unsupported")
    return kind


def _in_child(kind: str, data: bytes, max_pages: int, max_chars: int) -> dict:
    if not _slots.acquire(blocking=False):
        raise ImportRefused("busy")
    try:
        try:
            done = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.recipes.import_child",
                    kind,
                    str(max_pages),
                    str(max_chars),
                ],
                input=data,
                cwd=APP_ROOT,
                capture_output=True,
                timeout=TIME_LIMIT_SECONDS,
                env=_child_env(),
                check=False,
            )
        except subprocess.TimeoutExpired:  # run() kills the child before it raises
            logger.warning("brief import timed out after %ss", TIME_LIMIT_SECONDS)
            raise ImportRefused(
                "unreadable", "That file took too long to read. Paste the text in instead."
            ) from None
    finally:
        _slots.release()
    try:
        result = json.loads(done.stdout.decode("utf-8").strip().splitlines()[-1])
    except (ValueError, IndexError, UnicodeDecodeError):
        logger.warning("brief import child gave no answer (exit %s)", done.returncode)
        raise ImportRefused("unreadable") from None
    if "error" in result:
        code = result["error"]
        raise ImportRefused(code if code in MESSAGES else "unreadable")
    return result


def extract_text(data: bytes, filename: str) -> ExtractedText:
    """The text of `data`, named `filename` (only its extension is read). Cut at
    `brief_max_chars`, with `truncated` saying so. Raises `ImportRefused`."""
    settings = get_settings()
    kind = _kind(filename)
    if len(data) > settings.import_max_bytes:
        raise ImportRefused("too_big")
    if kind == "text":
        text, cut = clean_text(_decode(data), settings.brief_max_chars)
        pages = None
    else:
        found = _in_child(kind, data, settings.import_max_pages, settings.brief_max_chars)
        text, cut, pages = found["text"], found["truncated"], found["pages"]
    if not text:
        raise ImportRefused("no_text_layer")
    return ExtractedText(text=text, truncated=cut, pages=pages)
