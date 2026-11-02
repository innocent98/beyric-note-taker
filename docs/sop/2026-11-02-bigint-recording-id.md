# SOP: Vexa recording ids stored as bigint

## What shipped
Migration `0004` widens `meetings.vexa_recording_id` from `integer` to `bigint`, and `app/models.py` maps it as `BigInteger`. Branch `fix/bigint-recording-id`.

## Why
The first real meetings on the server (2026-11-02) all failed about a minute after the bot was let in, showing "Something went wrong while processing this meeting." Postgres logged `integer out of range` on `UPDATE meetings SET vexa_recording_id=$1::INTEGER`: Vexa's recording id was `676401909012`, beyond 32 bits. The spike's ids were small, so tests used `7` and never caught it. Widening the column alone was not enough, because SQLAlchemy casts the value to `INTEGER` from the model type.

## What's involved
`alembic/versions/0004_bigint_recording_id.py`, `app/models.py`, `tests/test_pipeline.py` (`test_recording_ids_beyond_32_bits_are_stored`), `tests/test_migrations.py` (`test_recording_id_widens_to_bigint_and_back`).

## Verification
New tests pass, with the pipeline, migration and model suites; ruff clean. On the server the column had already been widened by hand during the live call (after a backup), so `0004` is a no-op there.

## Operate / roll back
`alembic downgrade 0003` narrows the column again and fails, changing nothing, if a stored id no longer fits.

## Follow-ups
- The worker logs only the exception type (`DataError`), which hid the cause; the Postgres log had it. Consider logging the database error code too.
- `media_id` is not stored, so it has no such limit; ids received from Vexa are still checked to be plain integers.
