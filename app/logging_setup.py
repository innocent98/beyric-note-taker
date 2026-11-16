"""Make the web process's own INFO log lines reach `docker compose logs web`.

The worker and the scheduler call `logging.basicConfig(level=INFO)` in their `main()`. Web is
started by uvicorn (`--factory`), which configures only its own `uvicorn.*` loggers, so every
`logger.info` of an `app.*` module (the `hours_shadow` and `feature_gates_shadow` lines among
them) was dropped there: Python's last-resort handler prints WARNING and above only.

`configure()` is called once from the app factory. It adds a stderr handler and the INFO level to
the `app` logger only, so a library's INFO lines (httpx, openai) stay as quiet as before. It does
nothing when the root logger already has a handler: a test run (pytest's capture handlers), a
`--log-config` the operator chose, or a process that called `basicConfig` itself.
"""

import logging

FORMAT = "%(asctime)s %(name)s %(message)s"  # the worker's and the scheduler's format


def configure() -> None:
    app_logger = logging.getLogger("app")
    if logging.getLogger().handlers or app_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(FORMAT))
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False  # root has no handler, and one added later must not double a line
