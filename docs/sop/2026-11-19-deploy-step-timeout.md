# SOP: the deploy job waits 45 minutes, not 20

**What shipped.** `timeout-minutes` of the `deploy` job in `.github/workflows/deploy.yml` raised from 20 to 45, plus one paragraph in `deploy/README.md` section 8. Branch `ci/deploy-step-timeout`; the PR number is in the PR list. No change to `deploy/deploy.sh`, the deploy gate or anything that runs on the server.

## Why

Every deploy in the last stretch showed "The operation was canceled" at the step "Deploy over SSH". The server's `docker compose build` takes about 19 minutes, and with the checkout, backup, start and health checks the whole script runs past GitHub's 20-minute job limit. GitHub then cancels the SSH client only. The server went on and usually finished (the new code was live), but:

- the run showed cancelled/failed although the deploy was fine;
- the merge helper that polls the run printed "deploy not finished in 20 min";
- a re-run said "another deploy is running" until the first script ended, because `deploy.sh` holds `flock` on `.deploy.lock` for its whole life.

## How

Chosen: **(a) wait longer.** The step is one SSH command whose server side is `deploy/deploy.sh`; as long as the client stays attached, the run ends with the script's own exit code and the `deployed <sha>` line is the proof. 45 minutes leaves more than double the build time for a slow build, a backup and the 2-minute health wait, and is far under GitHub's own 6-hour job cap. Nothing else changes.

Why (a) is also the safer choice, from reading `deploy.sh`: the script writes to stdout and stderr (`echo "deployed ..."`, `build failed ... >&2`, `docker compose logs ... >&2`). If the client is cancelled mid-run, those pipes are closed and a later write can kill the script with SIGPIPE (`set -euo pipefail`, no trap). The dangerous write is the "build failed ... still running" line, which comes before the checkout back to the previous commit, so a cancelled client during a failing build could leave the checkout on the commit that failed to build. Keeping the client attached to the end avoids that whole class. Whether the server script survives a dropped client is not proven here (the previous lead saw it keep going); this fix does not depend on it.

Rejected:

- **(b) detach the server script (nohup/background) and poll a log file from the workflow.** Needs a new status file, a poll loop with its own bound, and a change to what the forced command does; it changes the deploy path and the failure reporting for little gain.
- **Cancel-proofing only (`flock` wait or a `trap` for SIGPIPE/SIGHUP in `deploy.sh`).** Treats the symptom; the run would still show cancelled. Could be added later, not needed now.
- **Making the build faster** (layer cache, build in CI and pull). The right long-term fix for the 19 minutes, but a separate piece of work.

## What's involved

| File | Change |
|---|---|
| `.github/workflows/deploy.yml` | `timeout-minutes: 20` to `45`, with a comment saying why |
| `deploy/README.md` (section 8) | paragraph: the job waits 45 minutes, do not shorten it below the build time |
| `docs/checklist/master-checklist.md` | deferred-follow-ups line and the 2026-11-19 addendum note |

## Verification

`actionlint .github/workflows/deploy.yml` passes locally. **Not verified live.** It can only be proven by the next real deploy of `main` after this merges: the run must end green after about 20 minutes or more, with the `deployed <sha>` line in the "Deploy over SSH" log. This PR itself changes only a workflow file and docs, so its own merge deploys quickly or not at all; watch the first deploy that rebuilds the images. Note also that `workflow_run` takes `deploy.yml` from `main`, so the new limit applies only once this PR is merged.

## Operate / roll back

Revert the PR (the limit goes back to 20). If a deploy hangs (not just slow), cancel the run in GitHub and check `.deploy.lock` and the script on the server before re-running. A build that really needs more than 45 minutes means the build itself needs attention.

## Follow-ups

- Shorten the 19-minute build (cache or build in CI), then the limit could come down.
- Optional hardening in `deploy.sh`: ignore SIGPIPE/SIGHUP so a dropped client cannot interrupt a rollback.
- The merge helper's own 20-minute poll limit (outside this PR) should be raised to match.
