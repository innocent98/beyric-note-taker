# SOP: the deploy retries a refused `git fetch` (2026-11-04)

**What shipped:** `deploy/deploy.sh` tries `git fetch origin main` up to 6 times, 20 seconds apart (`DEPLOY_FETCH_TRIES`, `DEPLOY_FETCH_DELAY`), before giving up with exit code 128 and the message "could not fetch origin main; nothing was changed". Two tests in `tests/test_deploy_script.py`.

**Why:** on 2026-11-04 four deploys (#58, then #60) failed at the first step, `git fetch`, with `git@github.com: Permission denied (publickey)` while the server's deploy key was fine (same fingerprint on GitHub, read-only, verified) and `ssh -T` succeeded some of the time. GitHub had an open "Git Operations" incident. Each failure needed a hand-run retry loop on the server (`for i in ...; do bash deploy/deploy.sh <sha>; rc=$?; [ $rc -ne 128 ] && break; sleep 20; done`), which worked.

**How:** a small `fetch_main` function. The fetch is the first step and nothing has been changed when it fails, so retrying is safe. Exit 128 is kept because that is what a failed fetch gave before, and the manual loop above keys on it. Alternative rejected: retrying the whole deploy, which could redeploy a version that was already rolled back.

**What's involved:** `deploy/deploy.sh`, `tests/test_deploy_script.py`. The retry helps only after one deploy has carried this script to the server (the running script is the one already on disk): the deploy that ships this change still needs GitHub to answer.

**Verification:** 14 tests in `tests/test_deploy_script.py` pass, ruff clean. Not exercised against the real GitHub.

**Operate / roll back:** nothing to set. To change patience, export `DEPLOY_FETCH_TRIES` and `DEPLOY_FETCH_DELAY` for the forced command. Revert the commit to roll back. If GitHub is down for longer than about two minutes, the manual loop above still applies.

**Follow-ups:** the GitHub Actions job gives up after the script's two minutes; if outages are common, consider longer patience or a fallback that sends the code from another machine.
