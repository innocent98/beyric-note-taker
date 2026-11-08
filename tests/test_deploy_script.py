"""deploy/deploy.sh against stand-ins for docker, git, curl, flock and sleep.

The stand-ins log every call and the commit that was checked out at the time, so a test can say
which version `docker compose up` ran for. The point is the rollback: it must run when `up` itself
fails (compose exits non-zero when a dependency is unhealthy, which `set -e` used to turn into a
dead script with the broken version left in place) and when health never comes.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "deploy" / "deploy.sh"
GATE = SCRIPT.with_name("gate.sh")
DUE = "2026-11-08T21:30:00Z"
OLD = "a" * 40
NEW = "b" * 40

DOCKER = """#!/usr/bin/env bash
state="$STUB_STATE"
head="$(cat "$state/head")"
echo "docker $* @${head:0:1}" >> "$state/log"
case "$1 $2" in
  "compose ps")
    if [[ "$*" == *"--status created"* ]]; then
      [ -f "$state/created" ] && echo next-created
      exit 0
    fi
    [[ "$*" == *" next"* && -f "$state/has_next" ]] && echo next-id
    exit 0 ;;
  "compose config")
    [ "$head" = "$STUB_NEW" ] && [ "$STUB_SCHED" = "1" ] && echo scheduler
    exit 0 ;;
  "compose exec")
    if grep -q scheduled_joins; then
      echo "gate-query @${head:0:1}" >> "$state/log"
      [ "$STUB_JOINS" = "error" ] && { echo "psql: connection refused" >&2; exit 1; }
      echo "$STUB_JOINS"
      exit 0
    fi
    echo "scheduler-beat-query @${head:0:1}" >> "$state/log"
    if [ -f "$state/beat" ]; then echo 1; else echo 0; fi
    exit 0 ;;
  "compose build")
    [ "$head" = "$STUB_BAD_BUILD_SHA" ] && exit 1
    exit 0 ;;
  "compose up")
    rm -f "$state/healthy" "$state/beat"
    [ "$head" = "$STUB_NEW" ] && [ "$STUB_BEAT" = "yes" ] && touch "$state/beat"
    if [ "$head" = "$STUB_NEW" ]; then behaviour="$STUB_NEW_UP"; else behaviour="$STUB_OLD_UP"; fi
    case "$behaviour" in
      ok) touch "$state/healthy"; rm -f "$state/created"; exit 0 ;;
      unhealthy) exit 0 ;;
      fail) touch "$state/created"; exit 1 ;;
    esac ;;
  "compose logs") exit 0 ;;
esac
if [ "$1" = "start" ]; then
  rm -f "$state/created"
  [ "$head" = "$STUB_OLD" ] && touch "$state/healthy"
  exit 0
fi
exit 0
"""
GIT = """#!/usr/bin/env bash
state="$STUB_STATE"
case "$1" in
  rev-parse) echo "$STUB_OLD" ;;
  checkout) echo "${@: -1}" > "$state/head"; echo "git checkout ${@: -1}" >> "$state/log" ;;
  merge-base) [ "$STUB_ON_MAIN" = "1" ] ;;
  fetch)
    n="$(cat "$state/fetches" 2>/dev/null || echo 0)"
    echo $((n + 1)) > "$state/fetches"
    [ "$n" -ge "${STUB_FETCH_FAILS:-0}" ] ;;
esac
"""
CURL = '#!/usr/bin/env bash\n[ -f "$STUB_STATE/healthy" ]\n'
NOOP = "#!/usr/bin/env bash\nexit 0\n"


@pytest.fixture
def server(tmp_path):
    project = tmp_path / "project"
    (project / "deploy").mkdir(parents=True)
    shutil.copy(SCRIPT, project / "deploy" / "deploy.sh")
    shutil.copy(GATE, project / "deploy" / "gate.sh")
    bin_dir, state = tmp_path / "bin", tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    for name, body in (
        ("docker", DOCKER),
        ("git", GIT),
        ("curl", CURL),
        ("flock", NOOP),
        ("sleep", NOOP),
    ):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    (state / "head").write_text(OLD)
    (state / "has_next").touch()
    (state / "healthy").touch()  # the old version is up when the deploy starts

    class Server:
        def deploy(
            self,
            *,
            new_up="ok",
            old_up="ok",
            bad_build=None,
            on_main=True,
            sha=NEW,
            scheduler=False,
            beat=True,
            fetch_fails=0,
            joins="",
            allow=None,
        ):
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "STUB_STATE": str(state),
                "STUB_OLD": OLD,
                "STUB_NEW": NEW,
                "STUB_NEW_UP": new_up,
                "STUB_OLD_UP": old_up,
                "STUB_BAD_BUILD_SHA": bad_build or "",
                "STUB_ON_MAIN": "1" if on_main else "0",
                "STUB_FETCH_FAILS": str(fetch_fails),
                "STUB_SCHED": "1" if scheduler else "0",
                "STUB_BEAT": "yes" if beat else "no",
                "STUB_JOINS": joins,  # what the gate's query prints: nothing, a time, or "error"
            }
            env.pop("ALLOW_DEPLOY_WITH_JOINS_DUE", None)
            if allow is not None:
                env["ALLOW_DEPLOY_WITH_JOINS_DUE"] = allow
            result = subprocess.run(
                ["bash", str(project / "deploy" / "deploy.sh"), sha],
                env=env,
                cwd=project,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.result = result
            return result

        @property
        def log(self) -> list[str]:
            path = state / "log"
            return path.read_text().splitlines() if path.exists() else []

        @property
        def calls(self) -> list[str]:
            """The log without the stand-in's own line for each `docker compose exec`."""
            return [line for line in self.log if not line.startswith("docker compose exec")]

        @property
        def head(self) -> str:
            return (state / "head").read_text().strip()

        def ups(self) -> list[str]:
            return [line for line in self.log if line.startswith("docker compose up")]

    return Server()


def test_a_healthy_deploy_does_not_roll_back(server):
    result = server.deploy()
    assert result.returncode == 0 and f"deployed {NEW}" in result.stdout
    assert server.head == NEW
    assert server.ups() == ["docker compose up -d --remove-orphans @b"]


def test_a_failing_up_still_reaches_the_rollback(server):
    # Compose exits non-zero when a dependency is unhealthy; the script must not die there.
    result = server.deploy(new_up="fail", old_up="ok")
    assert result.returncode == 1
    assert f"starting {OLD} again" in result.stderr
    assert server.head == OLD
    assert server.ups() == [
        "docker compose up -d --remove-orphans @b",
        "docker compose up -d --remove-orphans @a",
    ]
    assert "previous version is not healthy either" not in result.stderr


def test_a_new_version_that_never_turns_healthy_rolls_back(server):
    result = server.deploy(new_up="unhealthy", old_up="ok")
    assert result.returncode == 1 and f"starting {OLD} again" in result.stderr
    assert server.head == OLD
    assert server.ups()[-1] == "docker compose up -d --remove-orphans @a"
    assert len(server.ups()) == 2


def test_the_rollbacks_own_failing_up_does_not_stop_it_and_starts_stuck_containers(server):
    # `next` can be left in the Created state when compose gives up on a dependency. The rollback
    # must not leave it there, even when the rollback's own `up` fails too.
    result = server.deploy(new_up="fail", old_up="fail")
    assert result.returncode == 1
    log = server.log
    assert "docker compose ps -aq --status created @a" in log
    assert "docker start next-created @a" in log
    assert "previous version is not healthy either" not in result.stderr  # `start` brought it up


def test_when_the_previous_version_cannot_be_started_the_script_says_so(server):
    result = server.deploy(new_up="unhealthy", old_up="unhealthy")
    assert result.returncode == 1
    assert "previous version is not healthy either" in result.stderr


def test_a_failed_build_returns_to_the_previous_commit_and_touches_no_container(server):
    result = server.deploy(bad_build=NEW)
    assert result.returncode == 1
    assert server.head == OLD  # the next deploy must not mistake the new commit for the live one
    assert server.ups() == []
    assert "build failed" in result.stderr


def test_a_sha_not_on_main_is_refused_before_anything_changes(server):
    result = server.deploy(on_main=False)
    assert result.returncode == 1 and "not on origin/main" in result.stderr
    assert server.log == [] and server.head == OLD


def test_a_bad_argument_is_a_usage_error(server):
    result = server.deploy(sha="not-a-sha")
    assert result.returncode == 2 and "usage" in result.stderr


def test_a_scheduler_that_beats_after_the_deploy_passes(server):
    result = server.deploy(scheduler=True, beat=True)
    assert result.returncode == 0 and f"deployed {NEW}" in result.stdout
    assert "scheduler-beat-query @b" in server.log and server.head == NEW


def test_a_scheduler_that_never_beats_rolls_back_after_a_bounded_wait(server):
    result = server.deploy(scheduler=True, beat=False)
    assert result.returncode == 1
    assert "no heartbeat within 60 s" in result.stderr
    assert f"starting {OLD} again" in result.stderr and server.head == OLD
    assert server.log.count("scheduler-beat-query @b") == 15  # bounded, never forever


def test_the_rollback_to_a_commit_without_a_scheduler_does_not_wait_for_one(server):
    result = server.deploy(scheduler=True, beat=False)
    assert result.returncode == 1
    assert not [line for line in server.log if line == "scheduler-beat-query @a"]
    assert "previous version is not healthy either" not in result.stderr


def test_a_commit_without_a_scheduler_is_not_asked_for_a_beat(server):
    result = server.deploy(scheduler=False)
    assert result.returncode == 0
    assert not [line for line in server.log if line.startswith("scheduler-beat-query")]


def test_a_refused_fetch_is_tried_again_and_the_deploy_goes_through(server):
    result = server.deploy(fetch_fails=2)
    assert result.returncode == 0 and f"deployed {NEW}" in result.stdout
    assert result.stderr.count("git fetch failed") == 2
    assert server.head == NEW


def test_a_fetch_that_never_works_changes_nothing_and_exits_128(server):
    result = server.deploy(fetch_fails=99)
    assert result.returncode == 128 and "nothing was changed" in result.stderr
    assert result.stderr.count("git fetch failed") == 6
    assert server.log == [] and server.head == OLD  # no checkout, no build, no `up`


def test_a_due_join_refuses_before_anything_changes_and_names_its_time(server):
    result = server.deploy(joins=DUE)
    assert result.returncode == 1
    assert result.stderr.strip().count("\n") == 0  # one clear line
    assert f"a calendar join is due at {DUE}" in result.stderr
    assert "ALLOW_DEPLOY_WITH_JOINS_DUE=1" in result.stderr
    assert server.calls == ["gate-query @a"] and server.head == OLD  # no backup, checkout or build


def test_no_due_join_lets_the_deploy_through(server):
    result = server.deploy(joins="")
    assert result.returncode == 0 and f"deployed {NEW}" in result.stdout
    assert server.log.count("gate-query @a") == 1


def test_the_override_deploys_despite_a_due_join_and_says_so(server):
    result = server.deploy(joins=DUE, allow="1")
    assert result.returncode == 0 and f"deployed {NEW}" in result.stdout
    assert f"WARNING: a calendar join is due at {DUE}" in result.stderr
    assert server.head == NEW


@pytest.mark.parametrize("value", ["", "0", "yes", "true", "1 "])
def test_only_the_exact_value_1_overrides(server, value):
    result = server.deploy(joins=DUE, allow=value)
    assert result.returncode == 1 and "refusing" in result.stderr and server.head == OLD


def test_a_database_that_cannot_be_asked_refuses_and_changes_nothing(server):
    result = server.deploy(joins="error")
    assert result.returncode == 1
    assert "refusing: could not check for due calendar joins" in result.stderr
    assert server.calls == ["gate-query @a"] and server.head == OLD


def test_an_unexpected_answer_from_the_database_also_refuses(server):
    result = server.deploy(joins="ERROR: relation does not exist")
    assert result.returncode == 1 and "unexpected answer" in result.stderr
    assert "refusing: could not check" in result.stderr and server.head == OLD


def test_a_sha_not_on_main_is_refused_before_the_gate_asks_anything(server):
    result = server.deploy(on_main=False, joins=DUE)
    assert "not on origin/main" in result.stderr and server.log == []
