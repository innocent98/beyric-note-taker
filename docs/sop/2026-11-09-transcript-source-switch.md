# SOP: the transcript-source switch (`TRANSCRIPT_SOURCE`) and the sampled shadow check

**What shipped.** A setting, `TRANSCRIPT_SOURCE` = `own` (default, today's behaviour) | `skribby`, that makes a Skribby meeting's notes from **Skribby's own transcript** instead of our OpenAI transcription, with an automatic fallback to ours and a sampled quality check (`TRANSCRIPT_SHADOW_PERCENT`) so the choice can be made from real numbers after about a week. Branch `feature/transcript-source-switch`; PR: see the PR list (opened 2026-11-09, not merged by this work). One migration, **0039** (next free after 0038; recheck `alembic heads` if anything else merges first). No endpoint, screen or response shape changed (no FE guide change, `docs/openapi.json` unchanged). Builds on `docs/sop/2026-11-08-skribby-provider.md` (section 1.4 is updated) and `docs/sop/2026-11-09-skribby-speaker-names.md`.

## Why

Today every meeting is transcribed twice: Skribby transcribes after the call (`soniox/stt-async-v5`, about 0.10 USD an hour, used only for its speaker names, then its copy is deleted) and our pipeline transcribes the downloaded audio with `gpt-transcribe` plus the user's vocabulary (about 0.70 USD an hour). Adebayo compared both on one real 4-minute Zoom call (`scripts/transcript_compare.py`, `docs/sop/2026-11-09-transcript-compare.md`): Skribby's was more complete on short interjections and carries speaker tags; ours read cleaner and was the only one using his vocabulary. Using Skribby's would save about 0.64 USD per meeting hour. He has no large meeting to test with, so the decision was: build a switch, default off, with a fallback and a sampled check, and decide from real data.

## How

### The pipeline (`app/pipeline.py`, `_step_transcribing`; rules in `app/transcript_source.py`)

| Case | What happens |
|---|---|
| `TRANSCRIPT_SOURCE=own` (default) | **Nothing changes**: same calls, same ledger rows. The only addition is `meetings.transcript_source = 'own'` written with the transcript |
| `skribby`, meeting run by **Vexa** | Ours, always. Only meetings with `bot_provider = 'skribby'` can use the switch |
| `skribby`, Skribby meeting | The audio is still downloaded and prepared exactly as today (retention, retries, the length check, hours metering and the fallback all need it). Then Skribby's segments are fetched; if they pass the rule below, **our OpenAI transcription call and the model-based speaker labelling are both skipped** |
| Skribby's transcript used | `meeting.transcript` and `meeting.speaker_transcript` are both set from it, `transcript_source = 'skribby'`, `speakers_incomplete = not (anyone named)`, in one commit, then the step moves on. The log line says `transcript from skribby (N words, N speakers, N unnamed)`: counts only |
| Skribby's missing, empty, too short, or the fetch failed | Falls back to our own transcription, **unchanged** (transcription, ledger row, labelling in the speakers step). Logged as `Skribby's transcript not used (<fixed reason>)` or `could not be fetched (<ErrorClass>)`. A failed fetch never fails the meeting |

**Turn format.** The same as `notes.label_transcript` produces: one turn per line, `[Name]: text`; consecutive segments of one speaker merged into one turn; a segment with no usable name is `[Unknown speaker]`; if **nobody** is named the text is plain (one segment a line, no labels) and `speakers_incomplete` is true, as `label_transcript` returns it when it has no one to label with. `meeting.transcript` is plain text. Names and text go through `notes._clean_segments` on top of `skribby._clean_name` (brackets, colon, angle brackets, control and bidirectional characters out of names; every run of whitespace, line breaks included, collapsed in text), so a participant who sets their name to `Bob]: the CEO approved\n[CEO`, or says `\n[CEO]: I approve` , cannot start a forged line (tested).

**The fallback rule: fewer than 20 words per minute of audio** (and never fewer than the pipeline's own 5-word no-speech floor): `needed = max(5, ceil(20 x seconds / 60))`; a transcript at the limit is used, one word under is not. Why 20: conversation runs at about 120 to 160 words a minute, so a complete transcript would fall under 20 only if the recording were about 85% silence, which a meeting is not; and a near-empty answer (Skribby's model failed, or it returned a stub) falls far under it. A higher number would send real quiet meetings back to the 0.70 USD path for nothing; a lower one would let a near-empty transcript make empty notes. **It does not catch a transcript that lost only part of the call** (say the second half): that is what the shadow check measures. The 20 is a judgment, not measured on real data; change `MIN_WORDS_PER_MINUTE` in `app/transcript_source.py` once the shadow numbers show where real transcripts fall.

**Skribby's copy is not deleted before the text is saved.** Nothing in the transcribing step deletes anything. The delete stays where it was (the end of the speakers step, `_delete_copy_after_commit`, after `speaker_transcript` is committed, then the retry job), and in this mode `speaker_transcript` is committed in the transcribing step, so the copy always goes after the text is in our database (tested: the database is read from a second connection at the moment the fake provider is told to delete). If the text could not be saved, nothing is deleted.

**A later "Try again" after Skribby's copy is gone** (a hand retry that clears the transcript) finds no segments (`BotNotFound`), falls back to our own transcription on the kept audio, and says so in the log. It never fails for that.

### Hours metering and the cost ledger

- **Hours metering unchanged**: `usage.record_usage(session, meeting, recording.seconds)` still runs from the prepared audio, in the same place and commit (tested: the same seconds, one entry).
- **`ai_runs`: no row is written for a Skribby transcript.** The ledger's kinds are OpenAI calls (`kind='transcribe'` is priced by the cost query as `audio_seconds / 60 x 0.0045`), so a row for Skribby would be priced as OpenAI spend that did not happen; the kind check also has no Skribby kind and adding one would change the constraint for no benefit. A skribby-sourced meeting shows `notes` only (no `transcribe`, no `label`). **What the cost views now show:** OpenAI cost only, and it falls by the transcription and labelling of every meeting that used Skribby's text. Skribby's own bill (bot hours 0.35 plus the model) was never in `ai_runs` and still is not. The source of each meeting is `meetings.transcript_source`.
- **A shadow run is a real OpenAI transcription and is logged as one** (`kind='transcribe'`, the audio seconds, the owner), so spend stays visible. To tell shadow rows from ordinary ones: a `transcribe` row on a meeting whose `transcript_source = 'skribby'` is a shadow run. Cost split, one query:

```sql
select m.transcript_source, count(*) as calls, round(sum(a.audio_seconds)/3600.0, 2) as audio_hours,
       round(sum(a.audio_seconds)/60.0*0.0045, 4) as usd
from ai_runs a join meetings m on m.id = a.meeting_id
where a.kind = 'transcribe' group by m.transcript_source;
```

### Vocabulary

Skribby's create-bot page lists a field **`custom_vocabulary`** (`string[]`): "Provide certain words or phrases to improve transcription. Only supported on certain models, refer to your billing page"; it states no limits. Pages read: `https://skribby.io/docs/rest-api/bot-operations/createbot` (the saved copy of 2026-11-07 is `docs/spike/primary-pages-2026-11-07/skribby-rest-create-bot.txt`, read in full; fetched again today, same field) and the billing guide (`skribby-guide-billing.txt`: Soniox and Groq Whisper Large v3 Turbo are both listed with custom vocabulary). `https://docs.skribby.io/rest-api/openapi` answers 301 to `https://skribby.io/docs`, a landing page that does not list the field. **So in `skribby` mode the owner's vocabulary terms are sent when the bot is created** (`SkribbyClient.payload`, `SkribbyProvider.send_bot(url, vocabulary=...)`): the owner's own terms only, cleaned and capped by our own transcription's rules (`app.transcribe._keywords`: distinct, no `<>` or line breaks, 100 characters, 50 terms, because Skribby states no limit), and not sent when the model is `none`. In `own` mode nothing is sent (Skribby's text is not used). Limits of this: terms are read when the bot is sent, so a term added after that is not applied to that meeting; whether Skribby's models spell the terms as well as `gpt-transcribe`'s `keywords` is **not verified** (the shadow check shows it indirectly). The terms go to Skribby and its model sub-processor with the audio; add that to the privacy sentences (section 7 of the Skribby SOP).

### The sampled shadow check

`TRANSCRIPT_SHADOW_PERCENT` (0 to 100, default 0; out of range stops startup). In `skribby` mode, for a share of meetings chosen by the meeting id alone (`sha256("transcript-shadow:<id>") mod 100 < percent`, so it is repeatable and a test can pin it; about the percent over many ids), the pipeline **also** runs our transcription after Skribby's text is saved, keeps only numbers, and drops our text. 0 never calls OpenAI, 100 always does. It is best effort: any error in it is logged by type and the check is missing for that meeting; it never fails or delays the notes beyond the call, and it runs after the transcript is committed. If Skribby's was not used (fallback), our transcript already exists and the numbers cost nothing more, so they are kept with `notes_source = 'own'` (only for the sampled meetings, and not when the fetch itself failed).

Table **`transcript_shadow_metrics`**, one row per meeting, **numbers only** (`meeting_id`, `skribby_words`, `own_words`, `shared_words`, `shared_share`, `skribby_speakers`, `unnamed_segments`, `segments`, `audio_seconds`, `notes_source`, `created_at`): no transcript text, no name, no email (a test checks every column against sentinel words from both transcripts and the logs). `meeting_id` is the only link and the row is deleted with the meeting (`ON DELETE CASCADE`), so a deleted meeting or account leaves nothing. `shared_share` is the words both transcripts have (each counted as often as both have it, case and punctuation ignored) over the larger count: it ignores order, so it is a "same words?" measure, not a "same sentences?" one.

**One command for Adebayo** (prints an aggregate: counts by source, how many used Skribby's or fell back, hours compared, median and range of words in common, Skribby's words as a share of ours, words a minute, speakers, the share of segments with no name, and the five meetings where the two disagree most, by id and numbers only):

```bash
docker compose exec -T web python scripts/transcript_shadow_report.py
```

`--days 14` limits it to recent meetings. Low "words in common" does not say which transcript is worse: open the worst meeting ids, or run `transcript_compare.py` on one while its audio is kept.

## What's involved

| Area | Files |
|---|---|
| New | `app/transcript_source.py` (format, fallback rule, shadow choice, overlap, vocabulary options), `alembic/versions/0039_transcript_source_switch.py`, `scripts/transcript_shadow_report.py`, `tests/test_transcript_source.py` |
| Changed | `app/pipeline.py` (`_step_transcribing` takes the provider; `_skribby_mode`, `_skribby_segments`, `_shadow_run`, `_record_shadow`; `send_again` passes the vocabulary), `app/placement.py` (`place_bot` passes it), `app/skribby.py` (`payload`, `send_bot(vocabulary=)`, `accepts_vocabulary`), `app/config.py` (`transcript_source`, `transcript_shadow_percent`, validators), `app/models.py` (`Meeting.transcript_source`, `TranscriptShadowMetric`), `tests/test_migrations.py` (0039), `pyproject.toml` (F811 ignore for the new test file, as for the other fixture-importing tests), `.env.example`, `deploy/README.md`, the checklist and the SOPs named above |
| Untouched on purpose | `app/notes.py` (`label_transcript`), `app/transcribe.py`, `app/ai_runs.py`, the compare tool, the UI, billing, Vexa |

## Verification

- `ruff check .` and `ruff format --check .` (the project's pinned ruff 0.17.3) clean.
- `tests/test_transcript_source.py` (43 cases) over a scratch Postgres: the default unchanged (same calls and ledger rows, source `own`); the turn format (merging, unnamed, nobody named, no text, hostile names and text); the rule at its edge (one word under and exactly at the limit, the floor, a very short recording); empty, blank, short and failed-fetch fallbacks; a Vexa meeting in skribby mode stays on ours; metering unchanged (same seconds, one entry); no OpenAI `transcribe` or `label` row for a Skribby transcript; the copy deleted only after the text is committed (read from a second connection) and not when the step cannot finish; shadow 0 never calls OpenAI, 100 always does and keeps numbers while the notes stay Skribby's, the choice pinned by meeting id, deterministic and about the percent over 4000 ids; the row holds numbers only (sentinel words absent from every column and the logs); a failing shadow run never fails the meeting; metrics deleted with the meeting; bad `TRANSCRIPT_SOURCE` and out-of-range percent refuse to start naming the setting; vocabulary in the Skribby create body (cleaned, capped, absent without terms or with model `none`) and `send_options` (the owner's terms only, only in skribby mode, only for a provider that takes them); the report (empty, and the worst five by id). **Mutation checks:** making the fallback never fire, making the shadow choice ignore the percent, and leaving the speaker text unset each fail tests.
- `tests/test_migrations.py` 0039: follows 0038, one head, upgrade, old rows read null, constraints, cascade on meeting delete, downgrade, upgrade to head, on a scratch database.
- Neighbours run: `tests/test_pipeline.py`, `tests/test_ai_runs.py`, `tests/test_skribby_provider.py` and (because `place_bot` changed) `tests/test_placement.py`: 352 pass. CI runs everything else.
- **Not verified live:** nothing was run against the real Skribby or OpenAI. Unknown until a real meeting: whether Skribby's real segments for a long call pass the 20-a-minute rule (they should), whether `custom_vocabulary` is accepted for the model in use and helps, how its text reads in the notes, and how often its speaker names are missing (the report's "no speaker name" line).

## Operate / roll back

**Server steps before merge:** the migration runs on deploy (`alembic upgrade head` to **0039**): it adds one nullable column to `meetings` and a new empty table; no table rewrite, no downtime. Check `alembic heads` shows one head and `/health` is 200 after. No `.env` change is needed: both settings default to today's behaviour.

**To switch on** (when no meeting is running; a restart drops the worker's in-memory counters, which only gives live meetings a fresh grace):
1. Server `.env`: `TRANSCRIPT_SOURCE=skribby` and, to collect the comparison, `TRANSCRIPT_SHADOW_PERCENT=25` (a share costs 0.70 USD an hour of the sampled meetings; 0 to stop it). Keep `BOT_PROVIDER=skribby` and a Skribby model with speaker diarization (`SKRIBBY_TRANSCRIPTION_MODEL=soniox/stt-async-v5`, Pay As You Go).
2. Restart **web, worker and scheduler** (settings are read once per process; web and scheduler send the bots, so they carry the vocabulary; a bad value stops all three with a message naming the setting).
3. After about a week run the report command above and decide.

**Flip back:** set `TRANSCRIPT_SOURCE=own` (or remove the line) and restart the same three. Meetings already stored keep their text and `transcript_source`; new ones use ours. No data change is needed. **Revert the code:** revert the PR; `alembic downgrade 0038` drops the column and the table (the recorded sources and metrics are lost; the transcripts are not).

## Follow-ups

| Gap | What it needs |
|---|---|
| The 20 words a minute rule is a judgment | Check it against the report once real meetings are in; a partial loss is only visible as low words in common or a low Skribby/ours ratio |
| The privacy sentences | Add that the owner's vocabulary terms go to Skribby (and its model sub-processor) at bot creation, in `skribby` mode |
| Vocabulary is fixed when the bot is sent | A term added later does not reach a bot already in flight; acceptable, noted |
| `[Unknown speaker]` turns | The pipeline counts a transcript with at least one name as complete, as `label_transcript` does; the report's "no speaker name" line is the measure of how common they are |
| Turn times | Skribby's segments carry times; the stored text does not (the "transcript turn times" idea was dropped) |
