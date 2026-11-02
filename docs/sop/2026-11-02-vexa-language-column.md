# SOP: Vexa live segments failing on long language names

## What shipped
A live database change on the server (no PR): Vexa's `transcriptions.language` column went from `varchar(10)` to `varchar(32)`, applied by hand during meeting 5 on 2026-11-02 at about 15:36 UTC, with Adebayo's go-ahead. **It isn't in the repo yet**, so it isn't permanent (see Follow-ups).

## Why
During meeting 5 (Google Meet), Vexa logged `StringDataRightTruncationError: value too long for type character varying(10)` 198 times in 40 minutes. `whisper-1`, which makes Vexa's live hints, tags each segment with a language name, and on accented English it guessed `haitian creole` (14 characters). Vexa's own schema only allows 10, so every batch containing such a segment failed. Only 4 live segments had been stored. Live segments are our only source of speaker names, so the notes would have come back mostly "Unknown speaker". The audio and the post-call `gpt-transcribe` transcript were never affected.

## How
```sql
set lock_timeout = '3s';
alter table transcriptions alter column language type varchar(32);
```
Run in the `vexa-db` container (`psql -U $POSTGRES_USER -d $POSTGRES_DB`). Widening a varchar only changes the catalogue, not the rows, so it takes milliseconds. `lock_timeout` makes sure it can never queue behind Vexa's writes.

## Verification
- Stored segments for meeting 5 went from 4 to 116 within a minute. Vexa keeps the failed batch and retries it, so the backlog was saved too. Speakers found: 2.
- Zero truncation errors after the change.
- One side effect: a single `InvalidCachedStatementError` at 15:36:51, as asyncpg refreshed its cached query plans after the schema change. The worker's poll got one 500, logged "Vexa unreachable", and the next poll 30 s later was 200. The meeting stayed `bot_sent`.
- Languages whisper guessed for meeting 5: yoruba 65, english 44, haitian creole 4, latin 1, malay 1. Its language guess isn't reliable on this kind of speech.

## Operate / roll back
Check it with `select character_maximum_length from information_schema.columns where table_name='transcriptions' and column_name='language'` (expect 32). Rolling back isn't advised, because narrowing the column would fail on the stored long values.

## Follow-ups
- **Make it permanent:** the change is lost if the `vexa-db` volume is recreated, and a Vexa upgrade could run migrations over it. Add an idempotent step to `deploy/deploy.sh` (or a one-shot compose service) that runs the `alter` after `vexa` is up, and add it to the Vexa upgrade routine.
- Report it upstream to Vexa (the language column is too narrow for whisper's language names).
- Consider pinning the live-hint language to English in `.env.vexa`, if Vexa supports it, to stop the bad guesses. Test it on one call, because it could hurt French speech.
