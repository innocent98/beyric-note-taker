# SOP: Project setup (Task 0)

## What shipped
The Python project skeleton: settings, database session, `Meeting`, `Template` and `VocabularyTerm` models, the first Alembic migration (seeds four templates), Dockerfile, compose file and CI. Commit: `chore: set up project and data models`.

## Why
Every later task (Vexa client, audio, transcription, notes, worker, web) needs one agreed config, schema and test harness.

## How
- Settings via `pydantic-settings`, read from the environment or `.env`. Live transcription is always on in Vexa, so there is no setting for it.
- SQLAlchemy 2 with psycopg 3 (`postgresql+psycopg://`).
- `Meeting.status` (`bot_sent | processing | ready | failed`) and `Meeting.step` (`waiting | audio | transcribing | speakers | notes | done`) are plain strings guarded by database CHECK constraints, not Postgres enums, so later changes need only a constraint swap.
- Tests run against real Postgres, never SQLite.

## What's involved

| Path | Purpose |
|---|---|
| `pyproject.toml` | Dependencies, ruff and pytest config |
| `app/config.py`, `app/db.py`, `app/models.py` | Settings, `get_session()`, models |
| `alembic/versions/0001_initial.py` | Schema plus seeded templates General, Client call, Team update, Interview |
| `tests/test_models.py`, `tests/test_migrations.py` | Model defaults, constraints, migration round trip, single head |
| `Dockerfile`, `compose.yaml` | Postgres plus a `migrate` job; web and worker come later |
| `.github/workflows/ci.yml` | ruff check, ruff format check, pytest on a `postgres:17` service |
| `.env.example` | Tracked template; `.env` stays ignored |

## Verification
Tests, `ruff check .`, `ruff format --check .`, and an upgrade and downgrade on a scratch database (inside `test_migrations.py`) all pass against Postgres 17.

## Operate / roll back
```bash
python3.13 -m venv .venv && .venv/bin/pip install -e '.[dev]'
docker run -d --rm --name nt-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=notetaker_test -p 55433:5432 postgres:17
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55433/notetaker_test .venv/bin/pytest
.venv/bin/alembic upgrade head   # uses DATABASE_URL
```
Roll back with `alembic downgrade base`.

## Follow-ups
- The Docker image and compose file were not built here (shared Docker memory); CI does not build them either.
- The `Dockerfile` has no `ffmpeg` yet; Task 2 adds it.
