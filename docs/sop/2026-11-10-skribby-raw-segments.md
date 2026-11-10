# SOP: keep what Skribby sent for 7 days, and stop naming speakers by elimination

**What shipped.** (1) Per meeting, Skribby's raw transcript items and the name the app derived for each speaker tag (with the reason) are saved for 7 days, so a bad labelling can be diagnosed. (2) A new setting `SKRIBBY_NAME_BY_ELIMINATION` (default **true**, today's behaviour) that can turn off naming a tag by elimination. Adebayo's decision 2026-11-10: collect evidence first, decide later whether to remove elimination. **PR:** `feature/skribby-raw-segments` (number in the PR). **Migration 0040.** No endpoint, no screen, no API shape change (so no FE guide).

## Why

Adebayo's real meetings 29 and 30 on 2026-11-10 were labelled badly. In 29 a third speaker (him) was credited as someone who had not joined; in 30 there were 48 "Unknown speaker" lines and a prayer was credited to the wrong person. We could not tell whether Skribby's names or our alignment were at fault: the app deletes Skribby's copy minutes after the meeting and keeps only our own text. Prime suspect: the elimination rule in `app/skribby.py::_tag_names` (added in #161, `docs/sop/2026-11-09-skribby-speaker-names.md`), which names a tag with no `speaker_name` after the one name left on `potential_speaker_names`. When the real speaker is not on that list, or the list holds someone who is not in the call, the tag is named after the wrong person.

## How

| Piece | What it does |
|---|---|
| Evidence | `app/skribby.py::_evidence` builds, from the same body `map_bot` reads, a record `{items, tags, by_elimination}`. `items`: each transcript item with `speaker` (the diarization tag), `speaker_name`, the **names** in `potential_speaker_names` (no confidences), `start`, `end`, `text`. `tags`: for every tag `{name, how, note}`; `how` is `given` (its own segments carry one `speaker_name`), `elimination`, or `unknown`; `note` says why unknown (`several_names`, `no_candidates`, `elimination_off`, `unresolved`, `shared_claim`), and with elimination off `would_be` holds the name it would have taken. |
| Carried out | `BotMeeting.evidence` (new field, default None; `segments` is unchanged for Vexa and for the labeller). Building it can never stop a meeting being read (`_safe_evidence`, logs the exception class only). |
| Saved | `pipeline._step_speakers` calls `_keep_evidence` right after the lookup, in the commit that saves the speaker names and before the provider's copy is deleted. It runs in a savepoint: a failure rolls back only that write, logs the exception class (never the text), and the meeting still finishes. A retry of the step replaces the row (one row per meeting). |
| Deleted | After 7 days by `pipeline.delete_expired_audio` (the audio retention job; `EVIDENCE_RETENTION_DAYS = 7`, from the row's own `created_at`, whatever state the meeting is in); with the meeting (`services.delete_meeting` and the foreign key `ON DELETE CASCADE`); with the account (`deletion.purge_user`). |
| Read | Only `scripts/skribby_segments_report.py <meeting_id>`: items grouped by tag, the derived name and why, read-only. Never in an API answer or a page. |
| Elimination | `Settings.skribby_name_by_elimination`, default **true**: today's behaviour exactly, and each tag it names is recorded as `how: elimination` (with `by_elimination: true` on the row), so the report shows which names elimination produced. `false`: `_tag_names` names a tag only from the `speaker_name` its own segments carry; the elimination branch is still computed (the evidence shows what it would have said, `would_be`) but never applied. |

**Decision: a table, not a log line.** The text is what people said; a log would spread it into files with their own retention. A short-lived table with one delete job and a cascade is easy to prove gone. **Decision: the clock starts at the row's save time, not the meeting's start,** so a meeting that is slow to finish still keeps its evidence 7 days. **Rejected:** storing Skribby's whole body (it carries bot ids, links and per-word data we do not need); storing confidences (not needed for the diagnosis).

## What's involved

- `alembic/versions/0040_skribby_segment_evidence.py` (table `skribby_segment_evidence`: `meeting_id` unique, `items` and `tags` JSONB, `by_elimination`, `created_at`; index on `created_at`); `app/models.py::SkribbySegmentEvidence`.
- `app/skribby.py` (`_resolve_tags`, `_tag_names`, `_evidence`, `_safe_evidence`, `map_bot`), `app/bot_provider.py` (`BotMeeting.evidence`), `app/config.py` and `.env.example` (`SKRIBBY_NAME_BY_ELIMINATION`).
- `app/pipeline.py` (`_keep_evidence`, `_step_speakers`, `delete_expired_audio`), `app/services.py::delete_meeting`, `app/deletion.py::purge_user`.
- `scripts/skribby_segments_report.py`; tests `tests/test_skribby_provider.py`, `tests/test_skribby_evidence.py`, `tests/test_migrations.py` (0040), `pyproject.toml` (F811 ignore for the new test file, as for the other fixture-importing tests).

## Verification

Run on the Mac, bounded (CI runs the rest): `ruff check`, `ruff format --check`; `tests/test_skribby_provider.py` (both settings (default on, off tested), evidence shape and reasons, the failure path), `tests/test_skribby_evidence.py` (saved in the speakers step, none for Vexa, one row after a repeat, a failing save never stops the meeting or logs the words, 7-day retention, meeting delete, account delete, the report), `tests/test_migrations.py -k "0039 or 0040 or head"` (one head, upgrade, unique, cascade, downgrade, upgrade again), `tests/test_skribby_e2e.py`, `tests/test_transcript_source.py`, `tests/test_pipeline.py`, `tests/test_meeting_delete.py`, `tests/test_dockerfile_scripts.py`, and the purge/delete tests of `tests/test_admin.py`. **Not verified live:** no real Skribby meeting was run; the first real evidence row will be the proof.

## Operate / roll back

- **Server steps before merge:** migration 0040 runs on deploy. The only new env variable is the optional `SKRIBBY_NAME_BY_ELIMINATION`; leave it unset (= true, today's behaviour).
- **Read a meeting:** `docker compose exec -T web python scripts/skribby_segments_report.py <meeting_id>` (within 7 days of the meeting). It prints what people said: treat it like the transcript.
- **Turn elimination off:** `SKRIBBY_NAME_BY_ELIMINATION=false`, restart web, worker and scheduler.
- **Roll back:** revert the PR; `alembic downgrade 0039` drops the table (the evidence is disposable by design).

## Follow-ups

- Evidence is saved only in the speakers step. With `TRANSCRIPT_SOURCE=skribby` that step does not look Skribby up again (the names were saved in the transcript step), so no evidence is kept in that mode; add it there if that mode is switched on.
- Once evidence from a bad meeting is read, decide whether elimination is wrong, Skribby's names are wrong, or `label_transcript`'s alignment is, and whether to delete the elimination code.
