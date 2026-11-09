# SOP: Vexa behind a compose profile, so it can be stopped for good (2026-11-09)

**What shipped:** the five Vexa services in `compose.yaml` (`vexa-db`, `vexa-storage`, `vexa-storage-init`, `vexa`, `vexa-schema-fix`) are in the compose profile `vexa`. A plain `docker compose up -d` (what every deploy runs) no longer starts or touches them, so they can be stopped to free the server's memory and stay stopped. Branch `feature/vexa-compose-profile`; the PR number is on the branch. No migration, no app code change: `app/vexa.py` and `VexaProvider` are untouched.

## Why
`BOT_PROVIDER=skribby` (SOP `2026-11-08-skribby-provider.md`) moves bots to Skribby, so the Vexa containers (up to 3 GB for `vexa` alone, plus Vexa's database and storage) sit idle on a shared server. Stopping them by hand would not hold: the next deploy's `docker compose up -d` starts every service in the file. The way back to Vexa had to stay one command, with the data kept.

## How
| Decision | Reason / rejected alternative |
|---|---|
| One profile, `vexa`, on all five services | They depend only on each other (checked by a test), so one profile is enough and compose never meets a dependency in a profile that is off. A profile per service was rejected: nothing would use it |
| **No `depends_on` change** | `web`, `worker`, `scheduler` and `next` never had a `depends_on` on any Vexa service (only on `db`, `migrate`, `web`); the brief assumed otherwise. So there is no compose-version question (`required: false` is not used) and no start-order to preserve: while the profile is on, they start as before and the worker simply calls `vexa:8056` when it needs it |
| `VEXA_BASE_URL` left on web, worker, scheduler | Harmless while Vexa is down (a name that does not resolve is only read when Vexa is called) and it keeps the file identical when the profile is on |
| `deploy/deploy.sh`: comment only | It already never names a Vexa service and never waits on Vexa (health is the front door, 8020/8021). It does not set `COMPOSE_PROFILES`, so a rollback runs in the same mode as the deploy. Tests now pin that in both modes |
| Volumes, `.env.vexa`, `VEXA_*` secrets untouched | Stopping deletes nothing; `docker compose stop` keeps containers and volumes |
| `docker compose up -d vexa` still works | Compose turns on the profile of a service named on the command line (Docker's profiles page); `README` sections 4 and 8 keep their commands |
| Persistent "Vexa on": `COMPOSE_PROFILES=vexa` in the server `.env` | Compose reads it from `.env` too (checked: `config --services` lists Vexa), so the deploy's own `up` keeps Vexa running, `vexa-schema-fix` included |

## What's involved
| Path | Change |
|---|---|
| `compose.yaml` | `profiles: ["vexa"]` on the five services; a comment block with the way back |
| `deploy/deploy.sh` | 4 comment lines above `docker compose up`; no logic change |
| `deploy/README.md` | Table row, sections 4 and 5, checklist line, and the new "Stopping Vexa and bringing it back" in section 8 (the operator procedure; the commands live there, not here) |
| `tests/test_compose_vexa_profile.py` (new) | Reads `compose.yaml`: five services in the profile, the six app services in none, no app service depends on Vexa, Vexa depends only on Vexa, volumes and `env_file` kept. With Docker present: `config -q` in both modes, `config --profiles` is `vexa`, the two service lists differ exactly by the five, `up --dry-run vexa` works. Skipped without Docker |
| `tests/test_deploy_script.py` | The stand-in `docker` and `curl` now also record `COMPOSE_PROFILES` and the URLs; four new tests, each run with the profile off and on: a healthy deploy, "never names or waits on Vexa", the rollback, a failing `up` with a failing rollback |

## Facts found while building (each checked on 2026-11-09 with Docker Compose v2.40.3)
- **`${VEXA_DB_PASSWORD:?...}` and `${VEXA_STORAGE_SECRET:?...}` are checked even with the profile off** (interpolation runs before profiles are applied). `.env` must keep both while Vexa is stopped, or every `docker compose` command, the deploy's build included, fails. It fails before anything is touched.
- `.env.vexa` is read only when the profile is on (a missing file is fine while it is off).
- `up -d --remove-orphans` with the profile off leaves a Vexa container alone, running or stopped (throwaway project with a profiled service): a deploy neither stops nor removes it. `docker compose stop <service>` works with the profile off; `ps` without the profile still lists a Vexa container that exists.
- `docker compose down -v` with the profile **on** deletes the Vexa volumes. Never run it (it deletes `dbdata` too).
- `vexa-schema-fix` ran on every deploy; with the profile off it does not (it only matters after a Vexa upgrade, then run it by hand as the README says).
- A rollback to a commit from before this change starts Vexa again (that commit's compose has no profile): stop it again.

## Does anything touch Vexa at startup when `BOT_PROVIDER=skribby`? No.
Read in `app/main.py`, `app/worker.py` (`main` calls `skribby.check_region_unchanged` and builds the mailer), `app/scheduler.py` (`main`), `app/config.py` (validators only check the setting's value), `app/alerts.py`, `app/maintenance.py`: no Vexa health check, no startup refusal and no alert about Vexa. `build_provider()` for `vexa` only constructs a client; nothing connects until a call is made. **Runtime** calls to Vexa remain for meetings Vexa ran (`bot_provider='vexa'`), routed by `provider_for`:
1. A Vexa meeting still `bot_sent` or `processing`: `provider_for` sends the worker's calls to Vexa, which does not answer; the pipeline treats a provider error as retryable, so it should wait, not fail (read from the code, not run against a stopped Vexa).
2. `pipeline.delete_expired_audio` (hourly): for a Vexa meeting past `AUDIO_RETENTION_DAYS` the local audio is deleted, but Vexa's copy cannot be, so it logs `could not delete the provider's copy ... will retry`, leaves `audio_deleted_at` empty, and the recording stays in the `vexastorage` volume. **This can outlive the 7 days the privacy page states.** Two options, in the README: stop only when no Vexa copy is left, or stop now and run Vexa again for an hour once the window has passed.
3. `deletion.purge_user` for an account with Vexa meetings: waits for Vexa up to the 24-hour give-up, then purges with an `admin.purge_forced` alert.
None of these is fixed here: a fix would change retention or deletion behaviour, not compose.

## Verification
- `docker compose config --profiles` prints `vexa`. `config --services`: profile off `db migrate next scheduler web worker`; `COMPOSE_PROFILES=vexa` adds `vexa vexa-db vexa-schema-fix vexa-storage vexa-storage-init`; also with `COMPOSE_PROFILES=vexa` in `.env`. `config -q` passes in both modes.
- Mutation check: removing one profile, or giving `web` a `depends_on` on `vexa`, makes five of the new tests fail.
- Real stack, local, profile off, `BOT_PROVIDER=skribby` with placeholder keys: `docker compose up -d` started `db` (healthy), `migrate` (Exited 0), `web` (healthy), `worker`, `scheduler` (leader, heartbeat fresh), `next` (healthy); `8020/health`, `8020/healthz`, `8021/health` all 200; no `vexa` container, no `vexainternal` network, no Vexa volume; no Vexa, error or traceback line in the logs of worker, scheduler or web.
- `tests/test_deploy_script.py` and `tests/test_compose_vexa_profile.py`: 61 passed; `ruff check` and `ruff format --check` clean.
- **Not verified live:** stopping the real Vexa on the server; the effect of a reboot (`restart: unless-stopped` is documented by Docker as not restarting a container that was stopped, but not tried here); bringing the real Vexa back (its image was not pulled locally; only `up --dry-run` was run).

## Operate / roll back
Procedure with exact commands: `deploy/README.md`, section 8, "Stopping Vexa and bringing it back". Merge first (the deploy leaves running Vexa alone), then flip `BOT_PROVIDER` and confirm, then stop Vexa. Revert the PR to remove the profile: the next deploy starts Vexa again.

## Follow-ups
- Decide the Vexa recordings that outlive retention while Vexa is stopped (README options; or delete `vexastorage` after a backup, Adebayo's call).
- A one-off script to delete Vexa copies without running Vexa's app containers is not built.
- Remove `VEXA_*` from `.env` only when Vexa is retired for good, together with the `:?` checks in `compose.yaml`.
