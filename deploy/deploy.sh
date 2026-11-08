#!/usr/bin/env bash
# Deploy one commit of main to this server. Run by the CD workflow over SSH, where this script is
# the deploy key's forced command and the commit SHA arrives in SSH_ORIGINAL_COMMAND. It can also
# be run by hand: deploy/deploy.sh <full-sha>.
#
# Steps: check the SHA is on origin/main, back up the database, check out the SHA, build, start,
# and wait until both services answer: FastAPI (`web`, 127.0.0.1:8021) and the Next.js front door
# (`next`, 127.0.0.1:8020), including the path between them, and until the scheduler has written a
# fresh heartbeat. If the new version never turns healthy (or the build or `up` fails), the previous commit is started again. A migration that already ran is NOT undone; see
# deploy/README.md "Updating and rolling back".
#
# Everything runs inside main(), which bash reads in full before running it, so checking out a
# commit that changes this file cannot corrupt the run in progress.
set -euo pipefail

# The deploy gate (join_due_soon, deploy_gate): see deploy/gate.sh. Read here, before main starts,
# so the checkout below cannot change it mid-run.
# shellcheck source=deploy/gate.sh
. "$(dirname "${BASH_SOURCE[0]}")/gate.sh"

main() {
    local sha="${1:-${SSH_ORIGINAL_COMMAND:-}}"
    if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
        echo "usage: deploy.sh <full 40-character commit sha>" >&2
        return 2
    fi

    local project_dir
    project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    cd "$project_dir"

    exec 9>"$project_dir/.deploy.lock"
    flock -n 9 || { echo "another deploy is running" >&2; return 1; }

    # Nothing has changed yet, so a refused fetch can be tried again without harm. Exit 128 is what
    # a failed `git fetch` always gave, so a caller that retries on 128 keeps working.
    fetch_main || { echo "could not fetch origin main; nothing was changed" >&2; return 128; }
    if ! git merge-base --is-ancestor "$sha" origin/main; then
        echo "refusing: $sha is not on origin/main" >&2
        return 1
    fi

    # Nothing has changed yet, so refusing here costs nothing. Auto-join sends bots with nobody
    # pressing anything: do not restart the stack while one is about to go out.
    deploy_gate || return 1

    local previous
    previous="$(git rev-parse HEAD)"
    echo "deploying $sha (was $previous)"

    if [ -n "$(docker compose ps -q db 2>/dev/null)" ]; then
        BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/beyric-notes}" deploy/backup.sh
    fi

    git checkout --quiet --detach "$sha"
    if ! docker compose build --quiet; then
        # Nothing was started yet, so the running version is untouched: just put the checkout back,
        # or the next deploy would take the commit that failed to build for the live one.
        echo "build failed for $sha; still running $previous" >&2
        git checkout --quiet --detach "$previous"
        return 1
    fi
    # `up` exits non-zero when a dependency is unhealthy (a crash-looping web, for instance). Under
    # `set -e` that used to end the script right here, before the health check and the rollback
    # below, leaving the broken version in place. So it never decides anything on its own: the
    # health check does.
    if ! docker compose up -d --remove-orphans; then
        echo "docker compose up reported a failure; checking whether the services came up" >&2
    fi

    # The rollback below waits for the web and next health only: it must work for a commit that
    # has no scheduler, and a scheduler problem is a reason to roll back, not to wait.
    if wait_healthy && wait_scheduler_beat; then
        echo "deployed $sha"
        return 0
    fi

    echo "new version did not become healthy; starting $previous again" >&2
    docker compose logs --tail 50 next web worker migrate >&2 || true
    git checkout --quiet --detach "$previous"
    docker compose build --quiet || echo "build of $previous failed; using the images already here" >&2
    # --remove-orphans: a previous commit without the `next` service must not leave that
    # container running, because it holds 127.0.0.1:8020, which the old `web` publishes itself.
    docker compose up -d --remove-orphans || echo "docker compose up failed for $previous" >&2
    start_stuck_containers
    wait_healthy || echo "previous version is not healthy either; check the server" >&2
    return 1
}

# GitHub sometimes refuses the deploy key for a stretch (2026-11-04: an hour of failed deploys at this
# very line, during a Git Operations incident, while the key itself was fine). Tries and delay can
# be set with DEPLOY_FETCH_TRIES and DEPLOY_FETCH_DELAY.
fetch_main() {
    local tries="${DEPLOY_FETCH_TRIES:-6}" delay="${DEPLOY_FETCH_DELAY:-20}" i
    for i in $(seq 1 "$tries"); do
        git fetch --quiet origin main && return 0
        echo "git fetch failed (try $i of $tries)" >&2
        if [ "$i" -lt "$tries" ]; then sleep "$delay"; fi
    done
    return 1
}

# When compose gives up on a dependency it can leave the services that wait for it (`next`) in the
# Created state, never started. A rollback that does not start them leaves the site down.
start_stuck_containers() {
    local stuck
    stuck="$(docker compose ps -aq --status created 2>/dev/null || true)"
    if [ -n "$stuck" ]; then
        # shellcheck disable=SC2086  # one id per line, split on purpose
        docker start $stuck >/dev/null || echo "could not start $stuck" >&2
    fi
}

# A commit with a `scheduler` service must show a heartbeat newer than the moment `up` returned
# (the old scheduler is stopped by then, so an old beat cannot count). The scheduler is not in
# anyone's depends_on, so a crash-looping one never fails `up` or the web and next checks above;
# this is the only place it is noticed. Bounded (60 s: a start, plus a standby's takeover tick of
# up to 10 s), and skipped for a commit whose compose file has no scheduler.
wait_scheduler_beat() {
    docker compose config --services 2>/dev/null | grep -qx scheduler || return 0
    local since
    since="$(date +%s)"
    for _ in $(seq 1 15); do
        if [ "$(scheduler_beat_since "$since")" = "1" ]; then
            return 0
        fi
        sleep 4
    done
    echo "the scheduler wrote no heartbeat within 60 s" >&2
    docker compose logs --tail 30 scheduler >&2 || true
    return 1
}

scheduler_beat_since() {
    # SQL on stdin (quoting through `sh -c` is fragile). Prints 1 when a beat is newer than $1 + 1:
    # `date +%s` truncates, and the old scheduler's last beat can fall in the same second.
    docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA' 2>/dev/null <<SQL | tr -d '[:space:]'
select count(*) from app_state where scheduler_heartbeat_at > to_timestamp($1 + 1);
SQL
}

# Both services, and the hop between them. A commit from before the Next.js front door has no
# `next` and no port 8021: there 8020 is FastAPI itself, and only /health can be checked.
wait_healthy() {
    for _ in $(seq 1 30); do
        if front_door_healthy; then
            return 0
        fi
        sleep 4
    done
    return 1
}

front_door_healthy() {
    # Through the front door to FastAPI and the database (/health is not a Next page, so it is
    # proxied); this is the check that has always been here.
    curl -sf -o /dev/null http://127.0.0.1:8020/health || return 1
    if [ -z "$(docker compose ps -q next 2>/dev/null)" ]; then
        return 0
    fi
    curl -sf -o /dev/null http://127.0.0.1:8020/healthz || return 1   # Next itself
    curl -sf -o /dev/null http://127.0.0.1:8021/health || return 1    # FastAPI directly
}

main "$@"
exit $?
