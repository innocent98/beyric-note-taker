# SOP: CI runs the Python suite in 4 parallel shards (2026-11-04)

## What shipped
`ci.yml`'s single serial `test` job (one `pytest -v`, 2,876 tests, 9 to 10 minutes of tests inside a 12 to 13 minute run) is now **4 parallel shards** split by recorded timings, each with its own Postgres container. `ruff check` and `ruff format --check` run once in a new `lint` job. The apt packages come from a cache of `.deb` files. PR [#70](https://github.com/innocent98/beyric-note-taker/pull/70), branch `ci/shard-tests`. No application code or test changed.

## Why
Every fix took 12 to 13 minutes to show green, and every push to a PR restarted the clock. The tests are 495 seconds of work in one job, so the wall-clock is that, plus about 60 seconds of setup, and nothing ran at the same time.

## How
| Piece | Decision |
|---|---|
| Split | `pytest-split` (`--splits 4 --group N --splitting-algorithm least_duration --durations-path .test_durations`). Deterministic: the same durations file and the same tests give the same shards on every run. The committed `.test_durations` came from one serial CI run (2,876 entries, 495 s); the four shards come out at 676, 734, 735 and 731 tests, each estimated at 124 s. Checked locally with `--collect-only`: the four groups together hold all 2,876 tests, none twice |
| One Postgres per shard | `services:` belongs to the job, so each matrix leg gets its own container and database. The tests drop and create tables on one database, `test_migrations.py` creates a scratch database with a fixed name, and the scheduler tests take advisory locks: none of that can meet another shard's |
| `fail-fast: false` | A red shard does not cancel the others, so one run shows every failure |
| Lint once | New `lint` job: installs only `ruff` (version read from `pyproject.toml`'s pin, so the pin has one home), then `ruff check .` and `ruff format --check .`. Not in the shards |
| Summary job `ci` | Needs `changes`, `lint`, `test`, `web`, runs `if: always()`, and fails on any `failure` or `cancelled`. A matrix job's result is `failure` if any shard failed and `cancelled` if any was cancelled, so one bad shard fails `ci` (proved, below). Skipped jobs (path filters) still count as passing |
| pip cache | `setup-python` `cache: pip` with `cache-dependency-path: pyproject.toml` (the project has no requirements file) |
| apt cache | `.github/actions/system-deps`: on the first run it downloads the `.deb`s for `ffmpeg libpango-1.0-0 libpangoft2-1.0-0` and saves them (`actions/cache`, key holds the runner image version); later runs `dpkg -i` them with no network; if that fails it falls back to the normal `apt-get install`. Why: with 4 jobs hitting the Ubuntu mirror at once the apt step took 53 s, 163 s, 2 min 43 s and **7 min 53 s** (19 s when one job ran alone), which became the whole run's time |
| Unchanged | Path filters (and the `changes` job), `concurrency` with `cancel-in-progress` for pull requests only (a run on `main` is never cancelled), the `web` job, `deploy.yml` (it waits for the workflow named `ci`, which still exists) |

**Rejected: `pytest-xdist`.** It splits one run across processes sharing one database. Tried on a 150-test subset (`test_models`, `test_notes`, `test_migrations`, `test_scheduler`, `test_alerts`) with `-n 3`: **13 failed and 24 errors** against 0 serial, and no faster (41 s against 44 s). Making it safe needs a database per worker in `conftest.py` and a per-worker scratch name in `test_migrations.py`; shards with their own containers need neither.

**Rejected: a prebuilt image for the system packages.** More to maintain than the `.deb` cache and the cache does the same job.

## What's involved
- `.github/workflows/ci.yml`: `lint` job, `test` matrix, `ci` needs `lint`; the Python path filter also counts `.test_durations` and `.github/actions/`.
- `.github/workflows/test-durations.yml` (new, manual only): runs the whole suite serially and uploads `.test_durations` as an artifact.
- `.github/actions/system-deps/action.yml` (new): the apt cache.
- `.test_durations` (new, repo root, 324 KB): per-test timings.
- `pyproject.toml`: `pytest-split>=0.10` in the `dev` extra. That is the only non-workflow change; **no `conftest.py` change was needed**.
- Check names changed: `test` is now `test (1/4)` to `test (4/4)`, plus `lint`. `ci` is unchanged.

## Verification
| Run | Result |
|---|---|
| Before: `main` at `641baa0` (the commit this branch is built on), serial | `test` job 12 min 20 s, whole run about 12 min 35 s |
| Durations capture (serial, one job) | 9 min 16 s, 2,876 tests passed |
| After, first sharded run (cold caches, slow apt mirror) | green, 11 min 34 s; shards' pytest 1 min 53 s to 2 min 39 s; one shard waited 7 min 53 s on apt |
| After, warm apt cache, final head `4904cce` ([run 41866393662](https://github.com/innocent98/beyric-note-taker/actions/runs/41866393662)) | green, **3 min 58 s** from the run's creation to `ci` finishing; apt step 8 to 13 s; shards' pytest 2 min 22 s, 2 min 27 s, 2 min 45 s, 2 min 50 s |
| After, a second warm run on the head before this line (`a6472e6`, [run 41869156788](https://github.com/innocent98/beyric-note-taker/actions/runs/41869156788)) | green, 4 min 21 s from the first job starting to `ci` finishing (5 min 55 s from the run being created: it waited about 1 min 30 s for runners behind the run it had just superseded). Both are under the 6 minute goal |
| A failing test in one shard ([run 41868070145](https://github.com/innocent98/beyric-note-taker/actions/runs/41868070145)): a temporary `assert False` test added on top, then dropped | shard 2 failed (`1 failed, 758 passed`); shards 1, 3, 4 and `web` still ran to green; `ci` printed `changes=success lint=failure test=failure web=success` and failed. `lint` also failed on the same file (ruff B011), which shows lint gates too |

Not measured: a local sharded run (the machine was too loaded to give honest timings); coverage (the project has no coverage gate, so nothing needed a combine step).

## Operate / roll back
- **Required checks:** the repository is private on a plan without branch protection (`gh api repos/innocent98/beyric-note-taker/branches/main/protection` returns 403 "Upgrade to GitHub Pro or make this repository public"), so no check is required today. If protection is added, require only `ci`.
- **Add a test:** nothing to do. A test missing from `.test_durations` gets the average duration and is placed on a shard; the split stays deterministic.
- **Regenerate the timings** (after a large batch of new or removed tests, or when one shard runs clearly longer than the others): Actions, `test-durations`, Run workflow on the branch you want. When it finishes, download the `test-durations` artifact, put the file at the repo root as `.test_durations`, commit and open a PR. Or locally against a Postgres: `pytest --store-durations --clean-durations --durations-path .test_durations`. Timings from a loaded machine skew the split: prefer the workflow.
- **Change the shard count:** change `shard: [1, 2, 3, 4]` and `--splits 4` and the `test (${{ matrix.shard }}/4)` name together.
- **Refresh the apt cache by hand:** bump the `-v1` at the end of the key in `.github/actions/system-deps/action.yml`.
- **Roll back:** revert the PR (workflow and action files). `.test_durations` and the `pytest-split` line are harmless if left.

## Follow-ups
- Postgres runs with fsync on in CI; turning it off for the throwaway container would shave a little more off each shard if the 3 to 4 minute run ever matters again.
- The web job (about 1 min 45 s) now finishes well inside the test shards; nothing to do.
