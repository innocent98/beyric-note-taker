# SOP: operator scripts inside the Docker image (2026-11-04)

**What shipped:** the `Dockerfile` now copies `scripts/` into the image (`COPY scripts ./scripts`), and a test keeps it that way. Commands the docs give for the server, such as `docker compose exec web python scripts/backfill_extraction.py` and `scripts/delete_user.py`, now work.

**Why:** on 2026-11-03, after the AI extraction release (#49), `docker compose exec web python scripts/backfill_extraction.py` failed on the server with `can't open file '/srv/scripts/backfill_extraction.py'`. The image only copied `app/`, `alembic/`, `alembic.ini` and `pyproject.toml`; `delete_user.py`, documented in `deploy/README.md`, had the same problem. The backfill was run with `python -c` calling `workspace.backfill` instead (result `{'queued': 3, 'decisions_only': 0}`).

**How:** one `COPY` line, the whole directory, so scripts added later (the Phase C key re-encryption, C9) work without another Dockerfile change. The capture scripts (`scripts/capture_*.py`) come along; they are developer tools that only talk to a local stack and carry placeholder accounts. Alternative rejected: copying named files, which would break again on the next new script.

**What's involved:** `Dockerfile`, `tests/test_dockerfile_scripts.py` (the Dockerfile copies the directory; every script the SOPs and `deploy/README.md` run with `docker compose exec web|worker python scripts/X.py` exists).

**Verification:** the two new tests pass and ruff is clean. The image itself was not built locally (Docker's disk on the Mac is full); CI builds the image, and the first deploy should be followed by `docker compose exec -T web ls scripts` on the server.

**Operate / roll back:** no server steps (no env variable, no migration); the deploy rebuilds the image once. Revert the one line to roll back.

**Follow-ups:** none. `docs/sop/2026-11-03-ai-workspace-w1-extraction.md` already documents the backfill command that now works.
