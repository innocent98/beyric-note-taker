# SOP: CI apt cache key follows the runner image (2026-11-07)

## What shipped
`.github/actions/system-deps/action.yml`: the cached `.deb` set (ffmpeg, Pango) is now keyed by the Ubuntu release and the runner image version, and a failed cache install is repaired before the normal apt route runs. PR: see the PR that carries this file.

## Why
On 2026-11-07 about 12:44 UTC `ubuntu-latest` moved to a newer Ubuntu release (the cache list showed `setup-python-Linux-x64-26.04-Ubuntu` entries). Every Python test shard then failed at "install system packages" (exit 100, `dpkg: dependency problems ... libxml2`, `downgrading libhwy1t64 ...`) before any test ran, on PR #117 (re-run failed too). Root cause: the cache key was `apt-debs-Linux--ffmpeg-pango-v1`. The action meant to include the image version through `env.ImageVersion`, but that expression is empty (a runner process variable is not in the `env` context), so the key never changed and `.deb`s built for the old release were installed over the new one. The fallback "fall through to apt" then failed too, because the half-finished `dpkg -i` left packages unconfigured ("Unmet dependencies").

## How
| Change | Reason |
|---|---|
| New step reads `VERSION_ID` from `/etc/os-release` and `$ImageVersion` from the runner's environment into a step output; the cache key uses it and the suffix is bumped to `v2` | A new release or image gets its own cache; bumping `v2` also retires the poisoned `v1` entry |
| `sudo apt-get install -f -y ... \|\| true` before `apt-get update` on the fallback path | Repairs a failed `dpkg -i` so the fallback really "always works" |
Alternatives rejected: pinning `runs-on: ubuntu-24.04` (hides the next rollover and ages out), dropping the cache (the cache exists because four jobs on the mirror took 1 to 8 minutes).

## Immediate unblock (done before this PR)
The lead deleted the stale cache entry `10011029062` (`gh cache delete`); the next run rebuilt it for the new image and #117's tests passed.

## What's involved
`.github/actions/system-deps/action.yml` only. No app code, no migration, no server step.

## Verification
YAML parses. The proof is CI itself: this PR's test shards run the action: on the first run the `v2` key misses and installs through apt; a second run should show a cache hit and install from the saved `.deb`s. Not tested: a future image change (cannot be simulated here).

## Operate / roll back
Revert the PR. To force a refresh by hand, bump the trailing `v2`.

## Follow-ups
Check the second CI run after this merges shows the cache hit path working on the new release.
