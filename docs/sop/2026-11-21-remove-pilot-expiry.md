# SOP: the pilot-expiry job and its mail removed

**What shipped.** The 60-day pilot-expiry job (`app/pilot_expiry.py`, scheduler job `pilot_expiry`), its one mail ("Your pilot hours will expire"), its four settings (`PAID_PLANS_OPEN_ON`, `PILOT_EXPIRY_DAYS`, `PILOT_EXPIRY_NOTICE_DAYS`, `PILOT_EXPIRY_MIN_NOTICE_DAYS`) and its tests are gone. The trial, its 7-day / hours logic and everything else about hour grants are untouched. PR: see the checklist entry (branch `chore/remove-pilot-expiry`). **No migration, no table or column changed.**

## Why

Adebayo's decision: the pilot is over and no pilot grant will ever be made again; billing went live on 2026-11-21 on real Paystack and Flutterwave keys. The job only ever did something when `PAID_PLANS_OPEN_ON` was set, and the go-live closeout (`docs/sop/2026-11-21-go-live-closeout.md`) said it had to stay unset. It was dead code that could still hurt: setting the date by mistake would have ended grants and mailed people about a pilot that no longer exists.

## How, and what was decided

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| The job and its wiring | `app/pilot_expiry.py` deleted; the `if settings.paid_plans_open_on is not None:` block in `app/sweeps.py` (including its "set but not enforcing" warning) deleted. `trial_sweep` registration is unchanged. | Nothing else called the module except two scripts (below). |
| The mail | No separate template existed: the job called `alerts.notify_user(..., email_template=alerts.ALERT_TEMPLATE)`, the shared alert template every other notice uses. So the mail goes with the module; no template file to delete. | Nothing to keep. |
| The four settings | **Removed from `Settings`**, with their blank-line validator entries and the startup check that the notice days fit between the minimum and `PILOT_EXPIRY_DAYS`. | **`PAID_PLANS_OPEN_ON` was read only by pilot expiry** (`grep` over `app/`, `scripts/`, `tests/`, `web/`, `compose.yaml`, `.github/`: the job, its sweep entry, `scripts/trial_report.py`, `scripts/local_trial_e2e.py`, and tests). Nothing else uses it, so it did not need to stay. It stays unset on the server and now means nothing. |
| An old line in a server `.env` | **Harmless.** `Settings` is built with `extra="ignore"` (same situation as `BOT_PROVIDER` in #252), so a leftover `PAID_PLANS_OPEN_ON=...` (any value, even one the old startup check refused, such as `PILOT_EXPIRY_NOTICE_DAYS=99`) does not stop web, worker or scheduler. `compose.yaml` and `.env.example` never named these variables, so compose is unaffected. | Test: `tests/test_sweeps.py::test_old_pilot_expiry_lines_in_the_env_are_ignored_and_register_nothing`. |
| The env cleaner | The four names left the known layout and `DEFAULTS` and went into `UNUSED_NAMES` in `scripts/clean_env.py`, next to `BOT_NAME` and `BOT_PROVIDER`: a leftover line is listed as "removed, no longer read by the app" (name only, never the value) and `--apply` drops it, whatever the value. | Same pattern #252 used for Vexa. Without it the cleaner would call them "not recognised". Test: `tests/test_clean_env.py::test_leftover_pilot_expiry_lines_are_dropped_whatever_their_value`. |
| `scripts/trial_report.py` | The "PILOT EXPIRY" section and its three imports/helpers are gone; the report is now "Trial report". The trial section (and the "hold no trial or pilot grant" count, which is trial logic and still true for old pilot rows) is unchanged. | The section called `pilot_expiry._holders` and friends, which no longer exist. |
| `scripts/local_trial_e2e.py` | Step 4 (the pilot end) removed, with its helper `add_pilot`, three of the five fake people and the `PAID_PLANS_OPEN_ON` env line. Steps 1 to 3 (trial off, trial in shadow, enforce) are unchanged. The committed captures under `docs/fe-captures/trial/` are history and were not regenerated: the script now deletes only its own captures (01-23, `LAST_CAPTURE`), so the old pilot ones (24-33, cited by the FE billing guide) stay. | Step 4 imported the removed module. Not run in this change (see Verification). |
| Old `pilot` grant rows | **Untouched and still valid.** `hour_grants.kind = 'pilot'` stays a legal kind; `entitlements` still reads such rows; the trial's once-per-person rule still counts a past pilot grant. | The brief: no migration, rows stay readable. |
| The web | Not changed. `web/components/app/notifications-view.tsx` keeps its `pilot.expiry` link rule so an entry that already exists still opens billing. None can be created now (the job never ran in production: the date was never set). | An FE edit would need web checks for no gain. |

### Behaviour that changed

- **For anyone running now: none.** `PAID_PLANS_OPEN_ON` was unset, so the job was never registered and wrote nothing.
- If someone set `PAID_PLANS_OPEN_ON` from now on, nothing happens (no job, no end date written on grants, no mail).

## What's involved

| Path | Change |
|---|---|
| `app/pilot_expiry.py` | deleted |
| `app/sweeps.py` | import, the `pilot_expiry` registration and its warning removed; comment updated |
| `app/config.py` | the four settings, their validator entries and the notice-days check removed |
| `scripts/clean_env.py` | the four names moved from known/defaults to `UNUSED_NAMES` |
| `scripts/trial_report.py`, `scripts/local_trial_e2e.py` | pilot parts removed (above) |
| `deploy/README.md` | the settings row replaced by a one-line "removed" row (keeps `test_deploy_readme_has_a_row_for_every_setting...`'s intent and tells the owner a leftover line is ignored) |
| `tests/test_pilot_expiry.py` | deleted (21 tests of the job) |
| `tests/test_trial_report.py` | the three pilot tests and the `PAID_PLANS_OPEN_ON` assertion removed; trial tests unchanged |
| `tests/test_sweeps.py`, `tests/test_clean_env.py` | two new tests and one new test (above) |
| `docs/` | this SOP; a "removed" note on `2026-11-17-trial-and-pilot-expiry.md`, `2026-11-21-go-live-closeout.md` and the pilot paragraph of `fe-integration-guide-billing.md`; the checklist addendum |

Remaining mentions of `pilot_expiry` / `PAID_PLANS_OPEN_ON` after this change are deliberate: the clean_env comment and name list, the new tests, dated SOPs and launch docs (history), and the committed trial captures.

## Verification

Run locally (named files only, never the full suite), on a scratch Postgres (`docker rm -f pilot-expiry-removal-pg` afterwards): `ruff check .` and `ruff format --check .` with the project's pinned ruff 0.17.3 (the project's venv): clean; `pytest tests/test_trial_report.py tests/test_sweeps.py tests/test_clean_env.py tests/test_trial.py`: 172 passed. The project has no Python type-check step in CI (`.github/workflows/ci.yml` runs ruff and the tests).

Not verified: the rest of the suite and the web checks (CI runs them), `scripts/local_trial_e2e.py` (needs the real web and scheduler processes; lint-clean only), and the server (no access; nothing there needs a change).

## Operate / roll back

**Deploy.** Merge; CD runs. No server step is required. `PAID_PLANS_OPEN_ON` stays unset; if the server `.env` still has any of the four lines, they are ignored. Optional tidy, whenever convenient: `python3 scripts/clean_env.py /opt/beyric-note-taker/.env` (dry run) lists them; `--apply` drops them.

**Roll back.** Revert the PR on `main`. The job returns, off while `PAID_PLANS_OPEN_ON` is unset. No data was changed either way.

## Follow-ups

- Dated SOPs, launch docs and the trial captures still describe the pilot end as history; they are not rewritten.
- The `pilot.expiry` link rule in `web/components/app/notifications-view.tsx` and its test can go in a web change if the owner wants the last trace removed.
