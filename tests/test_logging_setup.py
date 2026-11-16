"""Web's own INFO lines reach its log (`app/logging_setup.py`). The shadow lines of `HOURS_MODE` and
`FEATURE_GATES_MODE` are `logger.info` in the web process, where nothing else configures logging.

A fresh interpreter, because pytest installs its own handlers on the root logger and `caplog`
forces levels: both would hide the bug this guards against."""

import logging
import subprocess
import sys
from pathlib import Path

from app import logging_setup

ROOT = Path(__file__).resolve().parent.parent

PROBE = """
import logging
from app.config import Settings
from app.main import create_app

create_app(Settings(_env_file=None, session_secret="x" * 40, cookie_secure=False, app_env="test"))
logging.getLogger("app.feature_gates").info("PROBE feature_gates_shadow line")
logging.getLogger("app.placement").info("PROBE hours_shadow line")
logging.getLogger("httpx").info("PROBE library line")
"""


def run_probe() -> str:
    done = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return done.stderr


def test_a_fresh_web_process_prints_the_apps_info_lines_and_not_a_librarys():
    printed = run_probe()
    assert "app.feature_gates PROBE feature_gates_shadow line" in printed
    assert "app.placement PROBE hours_shadow line" in printed
    assert "PROBE library line" not in printed


def test_it_does_nothing_when_logging_is_already_configured():
    """Under pytest the root logger has handlers (its capture), as it does under `--log-config`."""
    app_logger = logging.getLogger("app")
    before = (list(app_logger.handlers), app_logger.level, app_logger.propagate)
    assert logging.getLogger().handlers
    logging_setup.configure()
    assert (list(app_logger.handlers), app_logger.level, app_logger.propagate) == before
