#!/usr/bin/env bash
# The deploy gate: refuse to restart the stack while a calendar join is about to be dispatched.
# Sourced by deploy/deploy.sh (not run on its own); deploy/deploy-gate-check.sh tests it.
#
# Auto-join starts a bot with nobody pressing anything, so a deploy that restarts web, worker and
# scheduler at 09:59 can drop a 10:00 recording. A join counts as due when it is still waiting
# (`scheduled`, `no_capacity` or `dispatching`), its `run_at` is no more than 10 minutes away (or
# already past) and its `deadline_at` has not passed. Past the deadline the runner gives up on it
# anyway, so an old stuck row never blocks a deploy forever.
#
# The CD key runs deploy.sh as a forced command, so no flag can reach it from CI. The one override
# is the environment variable ALLOW_DEPLOY_WITH_JOINS_DUE=1, set on the server for one deploy
# (deploy/README.md, "Deploying while a join is due").
#
# Fail closed: when the database cannot be asked, the answer is "refuse", never "go ahead".

# Runs the SQL on stdin against the app's database and prints bare values. The check script
# replaces this function to point at a scratch database.
gate_psql() {
    docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -v ON_ERROR_STOP=1'
}

# One line: the earliest due `run_at` in UTC, or nothing when no join is due.
gate_sql() {
    cat <<'SQL'
select to_char(min(run_at) at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
from scheduled_joins
where state in ('scheduled', 'no_capacity', 'dispatching')
  and run_at <= now() + interval '10 minutes'
  and deadline_at >= now();
SQL
}

# Exit 0: nothing is due. Exit 1: a join is due; the earliest time (UTC) is printed on stdout.
# Exit 2: the database could not be asked, or it answered with something unexpected; a message is
# printed on stderr.
join_due_soon() {
    local out
    if ! out="$(gate_sql | gate_psql)"; then
        echo "could not ask the database whether a join is due" >&2
        return 2
    fi
    out="$(printf '%s' "$out" | tr -d '[:space:]')"
    if [ -z "$out" ]; then
        return 0
    fi
    if [[ "$out" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
        printf '%s\n' "$out"
        return 1
    fi
    echo "unexpected answer from the database: $out" >&2
    return 2
}

# What deploy.sh calls: 0 to go ahead, 1 to refuse (one clear line on stderr).
deploy_gate() {
    local due rc=0
    due="$(join_due_soon)" || rc=$?
    if [ "$rc" -eq 0 ]; then
        return 0
    fi
    if [ "${ALLOW_DEPLOY_WITH_JOINS_DUE:-}" = "1" ]; then
        if [ "$rc" -eq 1 ]; then
            echo "WARNING: a calendar join is due at $due; deploying anyway (ALLOW_DEPLOY_WITH_JOINS_DUE=1)" >&2
        else
            echo "WARNING: could not check for due joins; deploying anyway (ALLOW_DEPLOY_WITH_JOINS_DUE=1)" >&2
        fi
        return 0
    fi
    if [ "$rc" -eq 1 ]; then
        echo "refusing: a calendar join is due at $due (within 10 minutes); deploy after it has started, or set ALLOW_DEPLOY_WITH_JOINS_DUE=1 on the server to override" >&2
    else
        echo "refusing: could not check for due calendar joins (is the database up?); fix that, or set ALLOW_DEPLOY_WITH_JOINS_DUE=1 on the server to override" >&2
    fi
    return 1
}
