#!/usr/bin/env bash
# Deploy one commit of main to this server. Run by the CD workflow over SSH, where this script is
# the deploy key's forced command and the commit SHA arrives in SSH_ORIGINAL_COMMAND. It can also
# be run by hand: deploy/deploy.sh <full-sha>.
#
# Steps: check the SHA is on origin/main, back up the database, check out the SHA, build, start,
# and wait until both services answer: FastAPI (`web`, 127.0.0.1:8021) and the Next.js front door
# (`next`, 127.0.0.1:8020), including the path between them. If the new version never turns
# healthy, the previous commit is started again. A migration that already ran is NOT undone; see
# deploy/README.md "Updating and rolling back".
#
# Everything runs inside main(), which bash reads in full before running it, so checking out a
# commit that changes this file cannot corrupt the run in progress.
set -euo pipefail

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

    git fetch --quiet origin main
    if ! git merge-base --is-ancestor "$sha" origin/main; then
        echo "refusing: $sha is not on origin/main" >&2
        return 1
    fi

    local previous
    previous="$(git rev-parse HEAD)"
    echo "deploying $sha (was $previous)"

    if [ -n "$(docker compose ps -q db 2>/dev/null)" ]; then
        BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/beyric-notes}" deploy/backup.sh
    fi

    git checkout --quiet --detach "$sha"
    docker compose build --quiet
    docker compose up -d --remove-orphans

    if wait_healthy; then
        echo "deployed $sha"
        return 0
    fi

    echo "new version did not become healthy; starting $previous again" >&2
    docker compose logs --tail 50 next web worker migrate >&2 || true
    git checkout --quiet --detach "$previous"
    docker compose build --quiet
    # --remove-orphans: a previous commit without the `next` service must not leave that
    # container running, because it holds 127.0.0.1:8020, which the old `web` publishes itself.
    docker compose up -d --remove-orphans
    wait_healthy || echo "previous version is not healthy either; check the server" >&2
    return 1
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
