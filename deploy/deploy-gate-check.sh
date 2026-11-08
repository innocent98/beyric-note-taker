#!/usr/bin/env bash
# Proves the deploy gate (deploy/gate.sh) against a real Postgres holding the app's real schema.
# Creates a scratch database, builds the schema with the project's own migrations, seeds
# scheduled_joins and asserts the gate's answer; drops the database afterwards. Needs psql on the
# PATH, a Postgres to point at, and the project's Python environment for `alembic`.
#
#   docker run -d --name nt-pg-54351 -e POSTGRES_PASSWORD=pw -p 127.0.0.1:54351:5432 postgres:17-alpine
#   deploy/deploy-gate-check.sh
#
# Settings (environment): CHECK_PG_URL, a superuser URL on a maintenance database (default: the
# container above); ALEMBIC_CMD, how to run alembic (default `poetry run alembic`; point it at a
# virtualenv's `alembic` if you have one).
#
# It replaces `gate_psql` (the one function that talks to the database through docker compose) with
# a psql to the scratch database, so the real SQL and the real decision code run unchanged.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
admin_url="${CHECK_PG_URL:-postgresql://postgres:pw@127.0.0.1:54351/postgres}"
alembic_cmd="${ALEMBIC_CMD:-poetry run alembic}"
db="gate_check_$$"
# The same server and credentials, another database (and the SQLAlchemy spelling for alembic).
scratch_url="${admin_url%/*}/$db"
sqlalchemy_url="postgresql+psycopg://${scratch_url#postgresql://}"

psql_admin() { psql -X -q -tA -v ON_ERROR_STOP=1 "$admin_url" "$@"; }
psql_scratch() { psql -X -q -tA -v ON_ERROR_STOP=1 "$scratch_url" "$@"; }
trap 'psql_admin -c "drop database if exists $db" >/dev/null 2>&1 || true; psql_admin -c "drop database if exists ${db}_empty" >/dev/null 2>&1 || true' EXIT

psql_admin -c "create database $db" >/dev/null
# shellcheck disable=SC2086  # the command may be several words
# Migration 0006 creates the admin account from these two; the values are dummies for the scratch database.
migrate_log="$(cd "$here/.." && DATABASE_URL="$sqlalchemy_url" OWNER_EMAIL=gate-check@example.test OWNER_PASSWORD=dummy-for-the-check $alembic_cmd upgrade head 2>&1)" \
  || { printf '%s\n' "$migrate_log" | tail -n 15 >&2; echo "could not build the schema with: $alembic_cmd upgrade head" >&2; exit 2; }

# shellcheck source=deploy/gate.sh
. "$here/gate.sh"
gate_psql() { psql_scratch -v ON_ERROR_STOP=1; }

fail=0
check() {  # label, wanted, actual
  if [ "$2" = "$3" ]; then echo "PASS  $1: $3"; else echo "FAIL  $1: got $3, wanted $2"; fail=1; fi
}

n=0
# seed STATE RUN_OFFSET DEADLINE_OFFSET: a join whose run_at / deadline_at are those intervals
# from now (for example '-1 hour', '+5 minutes'). Foreign keys are switched off for the seeding
# session: the gate reads three columns of one table and needs no user behind the row.
seed() {
  n=$((n + 1))
  psql_scratch >/dev/null <<SQL
set session_replication_role = replica;
insert into scheduled_joins (user_id, platform, native_id, meeting_url, starts_at, ends_at, run_at, state, deadline_at)
values (1, 'zoom', 'm$n', 'https://zoom.us/j/$n', now() + interval '$2', now() + interval '$2' + interval '1 hour',
        now() + interval '$2', '$1', now() + interval '$3');
SQL
}
clear_joins() { psql_scratch -c "truncate scheduled_joins" >/dev/null; }
# The run_at of seeded row number N (its native_id is mN), in the gate's own format, read back from
# the row itself so a clock tick between seeding and asking cannot make the comparison flaky.
at_row() { psql_scratch -c "select to_char(run_at at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') from scheduled_joins where native_id = 'm$1'"; }

join_rc() {  # runs join_due_soon, sets rc and out
  rc=0; out="$(join_due_soon 2>/dev/null)" || rc=$?
}
gate_rc() {  # runs deploy_gate, sets rc and err (stdout is never used)
  rc=0; err="$(deploy_gate 2>&1 >/dev/null)" || rc=$?
}

# --- join_due_soon: the three joins the plan names, one at a time -------------------------------
clear_joins; join_rc
check "no joins at all: exit" 0 "$rc"

clear_joins; seed scheduled '-1 hour' '-45 minutes'; join_rc
check "join at -1 h (deadline long gone): exit" 0 "$rc"

clear_joins; seed scheduled '+5 minutes' '+20 minutes'; join_rc; want="$(at_row "$n")"
check "join at +5 min: exit" 1 "$rc"
check "join at +5 min: names its time" "$want" "$out"

clear_joins; seed scheduled '+20 minutes' '+35 minutes'; join_rc
check "join at +20 min: exit" 0 "$rc"

# --- the edges around them -----------------------------------------------------------------------
clear_joins; seed scheduled '-2 minutes' '+13 minutes'; join_rc
check "overdue join whose deadline has not passed: exit" 1 "$rc"

clear_joins; seed scheduled '+9 minutes' '+24 minutes'; join_rc
check "join at +9 min: exit" 1 "$rc"

clear_joins; seed scheduled '+11 minutes' '+26 minutes'; join_rc
check "join at +11 min: exit" 0 "$rc"

for state in no_capacity dispatching; do
  clear_joins; seed "$state" '+5 minutes' '+20 minutes'; join_rc
  check "$state join at +5 min: exit" 1 "$rc"
done

for state in joined completed skipped cancelled failed; do
  clear_joins; seed "$state" '+5 minutes' '+20 minutes'; join_rc
  check "$state join at +5 min is not waiting: exit" 0 "$rc"
done

clear_joins; seed scheduled '+8 minutes' '+23 minutes'; seed scheduled '+3 minutes' '+18 minutes'; seed scheduled '-1 hour' '-45 minutes'
join_rc; want="$(at_row $((n - 1)))"
check "several joins: exit" 1 "$rc"
check "several joins: the earliest due time is named" "$want" "$out"

# --- deploy_gate: what deploy.sh acts on, and the override --------------------------------------
clear_joins; seed scheduled '+5 minutes' '+20 minutes'; want="$(at_row "$n")"
unset ALLOW_DEPLOY_WITH_JOINS_DUE
gate_rc
check "due join, no override: refuses (exit)" 1 "$rc"
case "$err" in *"refusing: a calendar join is due at $want"*"ALLOW_DEPLOY_WITH_JOINS_DUE=1"*) m=ok ;; *) m="$err" ;; esac
check "due join, no override: one clear line naming the time and the override" ok "$m"
check "due join, no override: the message is a single line" 1 "$(printf '%s\n' "$err" | wc -l | tr -d ' ')"

ALLOW_DEPLOY_WITH_JOINS_DUE=1 gate_rc
check "due join, ALLOW_DEPLOY_WITH_JOINS_DUE=1: goes ahead (exit)" 0 "$rc"
case "$err" in *"WARNING: a calendar join is due at $want"*) m=ok ;; *) m="$err" ;; esac
check "due join, ALLOW_DEPLOY_WITH_JOINS_DUE=1: says it is overriding" ok "$m"

for v in 0 yes true ""; do
  ALLOW_DEPLOY_WITH_JOINS_DUE="$v" gate_rc
  check "due join, ALLOW_DEPLOY_WITH_JOINS_DUE='$v': still refuses (exit)" 1 "$rc"
done

clear_joins; seed scheduled '+20 minutes' '+35 minutes'; gate_rc
check "nothing due: goes ahead (exit)" 0 "$rc"
check "nothing due: says nothing" "" "$err"

# --- a database that cannot be asked: fail closed ------------------------------------------------
real_gate_psql="$(declare -f gate_psql)"
gate_psql() { psql -X -q -tA -v ON_ERROR_STOP=1 "postgresql://postgres:pw@127.0.0.1:1/nope"; }   # nothing listens
join_rc
check "database unreachable: join_due_soon exit" 2 "$rc"
gate_rc
check "database unreachable: refuses (exit)" 1 "$rc"
case "$err" in *"refusing: could not check for due calendar joins"*) m=ok ;; *) m="$err" ;; esac
check "database unreachable: says why" ok "$m"
ALLOW_DEPLOY_WITH_JOINS_DUE=1 gate_rc
check "database unreachable, override set: goes ahead (exit)" 0 "$rc"

# A reachable database without the table (a server that has not run the migration): also refuses.
psql_admin -c "create database ${db}_empty" >/dev/null
gate_psql() { psql -X -q -tA -v ON_ERROR_STOP=1 "${admin_url%/*}/${db}_empty"; }
join_rc
check "table missing: join_due_soon exit" 2 "$rc"
gate_rc
check "table missing: refuses (exit)" 1 "$rc"
psql_admin -c "drop database ${db}_empty" >/dev/null

# A database that answers with something that is not a time: refuses rather than guessing.
gate_psql() { echo "ERROR: something odd"; }
join_rc
check "odd answer: join_due_soon exit" 2 "$rc"

eval "$real_gate_psql"
exit $fail
