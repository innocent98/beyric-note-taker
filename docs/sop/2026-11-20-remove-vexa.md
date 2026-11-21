# SOP: Vexa removed from the repository

**What shipped.** The self-hosted bot host (Vexa) is gone from the code, `compose.yaml`, `deploy/`, the env files, the scripts and the tests. Skribby is the only bot provider; the `BotProvider` seam and `SkribbyProvider` are unchanged in behaviour. Old meetings that say `bot_provider = 'vexa'` stay readable and deletable and nothing asks a provider about them. PR: see the checklist entry (branch `chore/remove-vexa`). No migration.

## Why

Adebayo's decision, 2026-11-20: "remove Vexa from the repo". Production has run on Skribby only since 2026-11-09 (`BOT_PROVIDER=skribby`, `TRANSCRIPT_SOURCE=skribby`); the Vexa containers were stopped on the server on 2026-11-20 after the check `vexa_copy_left = 0` (every old Vexa meeting's recording copy was already deleted). What was left was dead weight and noise: three services and two volumes in compose, five `${...:?}` / `VEXA_*` variables a server `.env` had to carry, two deploy scripts, a Vexa-shaped fake under most tests.

## How, and what was decided

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| `BOT_PROVIDER` setting | **Removed.** `app.bot_provider.DEFAULT_PROVIDER = "skribby"` names the provider; `PROVIDER_FACTORIES` has one entry. | One provider makes the setting a trap (`vexa` would now crash startup). A server `.env` that still has the line starts fine: `Settings` uses `extra="ignore"` (test: `test_an_old_bot_provider_line_in_the_env_is_ignored...`). Kept instead: the seam, the factory table, `provider_for`. |
| Startup check for the Skribby key | Moved from a `Settings` validator to `skribby.require_configured`, called at the three entry points (web's `create_app` when it builds its own settings, `worker.main`, `scheduler.main`). | The validator only existed while Skribby was optional (`BOT_PROVIDER=skribby`). Now it would demand a key from every tool and test that builds `Settings()`. Same effect where it matters: the three processes still refuse to start without `SKRIBBY_API_KEY`, naming the setting. |
| Old `'vexa'` rows | `app.bot_provider.RETIRED_PROVIDERS = {"vexa"}`. `provider_for(..., 'vexa')` returns `RetiredProvider`: lookups and download say `BotNotFound`, stop and delete succeed at once, send raises. | The brief: treat the Vexa copy as already gone and never call anything. Deletion and retention needed no special case, because `BotNotFound` already means "nothing to wait for" (`app/deletion.py` `recording_to_delete`) and a no-op `delete_recording` is "already deleted". Rejected: falling back to the default provider for an unknown name (it would send a Vexa integer id to Skribby). |
| A Vexa meeting still in flight | `advance_meeting` fails it at once at the `waiting` and `audio` steps with `REASON_PROVIDER_GONE` ("This meeting was started with a recording service we no longer use, so it could not be finished."), which also frees its bot slot. Later steps (`transcribing`, `speakers`, `notes`) need nothing from the provider and finish normally (the speakers step already read `BotNotFound` as "no segments"). | No such meeting exists in production (checked before the server step); this just makes sure none can be polled for six hours. |
| DB columns | **Unchanged.** `meetings.vexa_meeting_id` and `meetings.vexa_recording_id` keep their names; the model maps them to `provider_bot_id` and `provider_recording_id` (`mapped_column("vexa_meeting_id", ...)`). The `bot_provider` server default stays `'vexa'`; the ORM always sets the value (`default=DEFAULT_PROVIDER`). | The brief: prefer no migration. A rename needs a deploy order (old code breaks on a renamed column while the new one is starting). Follow-up below. |
| Defaults that followed the provider | `BOT_MAX_LOCAL` now defaults to 20, `TEAMS_JOIN_ENABLED` to true (what production got under Skribby). `BOT_NAME`, `VEXA_*`, `bot_provider` are gone from `Settings`. `Settings.shown_bot_name` replaces `bot_name_for(provider)`. | Same values as before for the running server. Both are now in the cleaner's DEFAULTS, so a line equal to them is dropped. |
| Spike and bake-off leftovers | `docs/spike/start-instant-meeting/send_bot.py` and its two Vexa helpers and tests removed. The bake-off tool lost its hand-entered `note` command (the Vexa arm); the `vexa` arm name stays in `ALL_ARMS` so the recorded log (`docs/spike/bakeoff-results`) still reports. | They could not run any more. |

### Behaviour that changed

- **Skribby meetings: none.** Same calls, same states, same notes, same deletes. The pipeline, placement and join-runner code paths for Skribby were not touched beyond the rename of the provider parameter and fields (`vexa` → `provider`, `vexa_id` → `provider_id`, `vexa_meeting_id` → `provider_bot_id`).
- `advance_meeting` and `register_join_runner` no longer build the default provider up front: it is built when first needed (so a missing key is a retryable `BotProviderNotConfigured` inside the existing handlers, not an exception outside them).
- The admin bell title for a forced account purge now says "gone at the bot provider" (was "at Vexa").
- An old `'vexa'` meeting in flight now fails (above), and is not offered Retry (`retry_block` says `no_audio`: every try would fail again at once). Old finished ones behave as before; account deletion and retention skip any lookup for them (no per-row 404 error line) and retention does not build the Skribby provider for them.
- Compose: the 5 Vexa services, `vexapgdata`, `vexastorage`, `vexainternal`, `VEXA_BASE_URL` on three services and the `vexa` profile are gone; only `POSTGRES_PASSWORD` is required. `front`, `default`, `appdb` and `next`'s fixed address are unchanged.
- The env cleaner now also requires `SKRIBBY_API_KEY` (the processes refuse to start without it) and drops leftover `VEXA_*`, `BOT_NAME`, `BOT_PROVIDER` and `COMPOSE_PROFILES=vexa` as "no longer read by the app" (whatever the value), and `BOT_MAX_LOCAL=20` / `TEAMS_JOIN_ENABLED=true` as defaults.

## What's involved

| Path | Change |
|---|---|
| `app/vexa.py`, `deploy/vexa-api-key.sh`, `deploy/vexa-schema-fix.sh`, `deploy/vexa.env.example` | deleted |
| `app/bot_provider.py` | Vexa factory out; `DEFAULT_PROVIDER`, `RETIRED_PROVIDERS`, `RetiredProvider`, `is_retired`; `BotMeeting.provider_id`; `provider_for` returns the stand-in for a retired name |
| `app/pipeline.py` | retired meeting failed at `waiting`/`audio` (`REASON_PROVIDER_GONE`); lazy default provider; copy-retry query excludes retired providers |
| `app/deletion.py`, `app/services.py`, `app/placement.py`, `app/join_runner.py`, `app/gcal_sync.py`, `app/worker.py`, `app/send_now.py`, `app/web.py`, `app/api.py`, `app/calendar_api.py` | parameter `vexa` → `provider`; prose |
| `app/config.py` | removed `bot_provider`, `bot_name`, `vexa_base_url`, `vexa_api_key` and their validators; new defaults; `shown_bot_name` |
| `app/models.py` | attributes renamed, DB column names kept |
| `app/skribby.py`, `app/main.py`, `app/scheduler.py` | `require_configured`; the region check no longer reads the removed setting (it is a no-op while no key is set) |
| `compose.yaml` | Vexa block, volumes, network, env lines removed |
| `deploy/deploy.sh` (comment), `deploy/README.md` | env table rows, sections 3-5, "After Vexa was removed from the repo" |
| `.env.example`, `scripts/clean_env.py`, `tests/test_clean_env.py` | after #248 merged |
| `tests/` | `fake_vexa` → `fake_bot` etc. across the suite; the shared link-parser tests that lived in the removed Vexa test file (plain Zoom and Meet, canonical form, spoofed hosts) moved to `tests/test_meeting_url.py`; new `tests/test_compose_services.py` (replaces the profile test), `tests/test_retired_provider_meetings.py`; contract tests now `FakeProvider` + `SkribbyProvider` |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | two docstring sentences only |

## Remaining mentions of the word (grep, case-insensitive, over `app/ compose.yaml deploy/ scripts/ tests/ web/ .env.example .github`)

All deliberate. `app/bot_provider.py` and `app/deletion.py` (the retired-name constant and the comments that explain it), `app/models.py` (the two database column names and the `'vexa'` server default, which are the schema), `deploy/README.md` and `deploy/deploy.sh` (the owner's steps after the removal, with the real names to look for on the server), `scripts/clean_env.py` (the leftovers it removes), and tests that pin one of those: the data value `'vexa'` on a meeting row (`test_skribby_e2e`, `test_skribby_provider`, `test_provider_contract`, `test_bot_name`, `test_bot_invite_tip`, `test_rebuild_meeting_from_evidence`, `test_ai_cost_report`, `test_transcript_source`, `test_retired_provider_meetings`), the recorded bake-off arm (`test_bakeoff_tool`), leftover env lines (`test_clean_env`), and "nothing of it is in compose" (`test_compose_services`). `tests/test_migrations.py` is untouched (the migration that added the column, and the column names). Not in scope and untouched: dated SOPs, specs, plans, FE captures and guides, `docs/spike`, `tools/bakeoff` (its recorded arm), alembic history.

## Verification

Run locally on a scratch Postgres (`docker rm -f vexa-removal-pg` afterwards), only named files, never the full suite:

- `ruff check .` and `ruff format --check .` (the pinned 0.17.3, the project's venv): clean.
- Named files, all green after the fixes below: `test_provider_contract`, `test_pipeline`, `test_pipeline_not_joined`, `test_transcript_source`, `test_skribby_evidence`, `test_end_meeting`, `test_bot_live_state`, `test_join_refusal`, `test_placement`, `test_join_runner`, `test_gcal_sync`, `test_send_now`, `test_skribby_provider`, `test_skribby_e2e`, `test_chat_notice`, `test_bot_name`, `test_bot_name_paths`, `test_bot_invite_tip`, `test_admin` (account deletion), `test_meeting_delete`, `test_retired_provider_meetings`, `test_compose_services`, `test_dockerfile_scripts`, `test_deploy_script`, `test_clean_env`, `test_bot_provider_fake_e2e`, `test_vocabulary_pass`, `test_microsoft_oauth`, `test_ai_cost_report`, `test_bakeoff_tool`, `test_spike_start_instant_meeting`, `test_rebuild_meeting_from_evidence`, `test_transcribe`, `test_migrations`, `test_api`, `test_web`, and the files that touch the changed defaults (capacity, Teams, bot name): `test_alerts`, `test_calendar_connect`, `test_calendar_settings_api`, `test_calendar_api`, `test_calendar_microsoft_words`, `test_graph_client`, `test_graph_events`, `test_gcal_events`, `test_gcal_rules`, `test_hours_enforce`, `test_hours_reserve`, `test_mail_summary`, `test_models`, `test_organizer_bot`, `test_template_delete`, `test_usage`, `test_meeting_url`, `test_skribby_live_check`.
- What the runs found, all from defaults that changed: tests that assumed capacity 1 or Teams off now set it (`bot_max_local=1`, `teams_join_enabled=False`) where the test is about that; `bot_name` tests moved to `skribby_bot_name`; the startup-key tests moved to `require_configured`; the old Vexa routing tests became retired-provider tests.
- `docker compose config -q` with a `.env` holding only `POSTGRES_PASSWORD`: passes. Also passes with leftover `COMPOSE_PROFILES=vexa`, `VEXA_DB_PASSWORD` and `BOT_PROVIDER` lines in `.env`. `config --services` = `db migrate worker scheduler web next`; no profiles.
- **`--remove-orphans` removes the stopped containers, shown with a throwaway compose project (2026-11-20):** a stopped service in an inactive profile survived `up -d --remove-orphans` while it was still defined; after its definition was removed from the file, the same command printed `Container ... Stopped / Removing / Removed`, and `docker volume ls` and `docker network ls` still listed the project's volume and custom network. Scratch project cleaned up. `deploy/deploy.sh` runs exactly `docker compose up -d --remove-orphans` (lines 71 and 88), so the next deploy removes the Vexa containers and nothing else.
- Not verified: the server (no access), the real Skribby API (nothing in its code changed), the web `npm` lint/typecheck/test/build (only a test title, two script strings and two comment lines in `schema.d.ts` changed; CI runs them), and every other test file in the suite, which CI runs.

## Operate / roll back

**Deploy.** Merge; CD runs. The first deploy removes the stopped Vexa containers (containers only). Then, on the server, in `deploy/README.md` section 8 "After Vexa was removed from the repo": check `docker compose ps -a | grep -i vexa` is empty, and `docker volume ls | grep -i vexa` still lists the two volumes.

**Optional, separate step Adebayo approves (never run by a session):** delete the Vexa volumes, network and images by hand. This cannot be undone, so re-run the `vexa_copy_left` check first and stop if it is not 0.

```bash
cd /opt/beyric-note-taker
docker compose exec -T db psql -U postgres -d notetaker -c "select count(*) as vexa_copy_left from meetings where bot_provider = 'vexa' and status in ('ready', 'failed') and audio_deleted_at is null and (vexa_recording_id is not null or vexa_meeting_id is not null);"
docker volume ls | grep -i vexa        # use the exact names printed
docker volume rm <project>_vexapgdata <project>_vexastorage
docker network rm <project>_vexainternal
docker image rm vexaai/vexa-lite:v012 versity/versitygw:v1.8.0
rm -f /opt/beyric-note-taker/.env.vexa
```

Then run `python3 scripts/clean_env.py /opt/beyric-note-taker/.env` (dry run first): it now names the leftover `VEXA_*`, `BOT_NAME`, `BOT_PROVIDER` lines; `--apply` drops them. They are harmless until then.

**Roll back.** Revert the PR on `main`. A revert needs the two volumes, the `VEXA_*` secrets (`VEXA_DB_PASSWORD` opens the database in `vexapgdata`) and `.env.vexa` back: keep `.env.bak-*` (the cleaner writes it) and `.env.vexa` as long as a revert is possible, and run the optional deletion only when sure it never is (the README says so). After it, Vexa would start empty. There is no setting to flip back any more.

**On the retired-provider stand-in (a security-review note, acknowledged).** `RetiredProvider` treats an old meeting's provider copy as deleted without asking anyone. That is true only because the copies were verified gone before this removal (`vexa_copy_left = 0` on 2026-11-20). If it were ever false, the data would sit in the `vexastorage` volume on the server until the owner removes it; the app would not delete it. A delete of a recording by the stand-in is logged (`recording N of retired provider 'vexa' is taken as already deleted`), so there is an audit line. A startup flag that refuses to trust the stand-in was considered and not built: it would guard a premise the owner has already checked, with a switch nobody can usefully set.

## Follow-ups

- Migration `0055`: rename the two columns and set the `bot_provider` server default to `skribby`, then drop the `mapped_column("vexa_...")` names. Needs a deploy order (new code first, or a compatibility view). Not needed for anything today. **The trap until then:** the database default of `meetings.bot_provider` is still `'vexa'`, so a raw SQL insert that omits it makes a "retired" meeting (the ORM always sets it). The one raw insert in the repo, `web/scripts/seed-owner-states.sql`, now sets `'skribby'`; the inserts in `tests/test_migrations.py` are history.
- The server's `.env.vexa` file and the three Vexa images/volumes (optional step above).
- Docs that tell the history of Vexa (specs, dated SOPs, FE guides) were left as written.
