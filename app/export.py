"""Build a PDF or Word file of a meeting's notes, on request, and never keep it.

Each build runs in a child process (`app/export_render.py`): one at a time per web process, with
a hard time limit. A render that hangs is killed, and the memory a big document needed goes back
to the system instead of staying in the long-lived web process (spike EX1,
docs/spike/export-pdf-memory.md). Nothing is written to disk by this module and nothing is
cached: the file exists only in the response.
"""

import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.notes_document import NotesDocument

logger = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parent.parent

# Below nginx's `proxy_read_timeout` (60 s, deploy/nginx.conf) so a slow render answers with our
# own JSON error instead of the edge's gateway page.
TIME_LIMIT_SECONDS = 45
FORMATS = {
    "pdf": ("application/pdf", "pdf"),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
}

_one_at_a_time = threading.Semaphore(1)


class ExportBusy(Exception):
    """Another export is running in this process."""


class ExportFailed(Exception):
    """The file could not be built. `timed_out` tells a slow render from a broken one."""

    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


def _child_env() -> dict[str, str]:
    """Only what the renderer needs to find its libraries: none of the app's settings."""
    env = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C.UTF-8", "XDG_CACHE_HOME": "/tmp"}
    for name in ("DYLD_FALLBACK_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"):
        if name in os.environ:  # a developer's Mac finds Pango through Homebrew's path
            env[name] = os.environ[name]
    return env


def build_file(doc: NotesDocument, fmt: str) -> bytes:
    """The file for `doc` as `fmt` ("pdf" or "docx").

    Raises ExportBusy when another export is running (the caller answers 503) and ExportFailed
    when the child fails or runs past the time limit.
    """
    if fmt not in FORMATS:
        raise ValueError(fmt)
    if not _one_at_a_time.acquire(blocking=False):
        raise ExportBusy()
    started = time.monotonic()
    try:
        try:
            done = subprocess.run(
                [sys.executable, "-m", "app.export_render", fmt],
                input=doc.model_dump_json().encode(),
                cwd=APP_ROOT,  # the same `app` package as this one (the image runs it from /srv)
                capture_output=True,
                timeout=TIME_LIMIT_SECONDS,
                env=_child_env(),
                check=False,
            )
        except subprocess.TimeoutExpired:  # run() kills the child before raising
            logger.error("export: the %s render ran past %s seconds", fmt, TIME_LIMIT_SECONDS)
            raise ExportFailed("the render ran past the time limit", timed_out=True) from None
        if done.returncode != 0 or not done.stdout:
            # The renderer's stderr can quote the notes; keep its last line out of the logs'
            # reach and record only the exit status.
            logger.error("export: the %s render exited with status %s", fmt, done.returncode)
            raise ExportFailed(f"the render exited with status {done.returncode}")
        # How long it took and how much memory the child peaked at: the numbers the spike
        # measured once, kept visible so a slow or heavy export shows up in the logs.
        peak = re.search(rb"peak_rss_kb=(\d+)\s*$", done.stderr)
        logger.info(
            "export: %s built in %.1f s, %d bytes, renderer peak %s MiB",
            fmt,
            time.monotonic() - started,
            len(done.stdout),
            int(peak.group(1)) // 1024 if peak else "?",
        )
        return done.stdout
    finally:
        _one_at_a_time.release()
