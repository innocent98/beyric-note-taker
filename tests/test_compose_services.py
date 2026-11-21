"""compose.yaml defines exactly the app's services, and needs only the app's own variables.

Read as text, like the other compose tests; the last group asks `docker compose config` (skipped
where Docker is absent). Vexa was removed on 2026-11-20 (docs/sop/2026-11-20-remove-vexa.md): no
service, volume, network or variable of it is left, so a server `.env` without any `VEXA_*` key
must still give a valid configuration.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
COMPOSE = (ROOT / "compose.yaml").read_text()
APP_SERVICES = {"db", "migrate", "worker", "scheduler", "web", "next"}


def _services() -> dict[str, str]:
    """Each service's block of text, by name, from the `services:` section."""
    section = COMPOSE.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    blocks: dict[str, str] = {}
    name = None
    for line in section.splitlines():
        header = re.match(r"^  ([a-z][a-z0-9-]*):\s*$", line)
        if header:
            name = header.group(1)
            blocks[name] = ""
        elif name is not None:
            blocks[name] += line + "\n"
    return blocks


def _depends_on(block: str) -> set[str]:
    """The service names under a block's `depends_on:` (the long form: one name per line)."""
    found: set[str] = set()
    inside = False
    for line in block.splitlines():
        if re.match(r"^    depends_on:\s*$", line):
            inside = True
        elif inside and re.match(r"^      ([a-z][a-z0-9-]*):\s*$", line):
            found.add(line.strip().rstrip(":"))
        elif inside and re.match(r"^    \S", line):
            inside = False
    return found


def test_the_services_are_the_apps_own():
    assert set(_services()) == APP_SERVICES


@pytest.mark.parametrize("name", sorted(APP_SERVICES))
def test_no_service_is_in_a_profile(name):
    # A profile on any of these would stop a plain `up -d` from starting the site.
    assert "profiles:" not in _services()[name]


@pytest.mark.parametrize("name", sorted(APP_SERVICES))
def test_every_dependency_is_a_defined_service(name):
    assert _depends_on(_services()[name]) <= APP_SERVICES


def test_nothing_of_the_removed_bot_host_is_left():
    assert "vexa" not in COMPOSE.lower()


def test_the_only_required_variable_is_the_database_password():
    required = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*):\?", COMPOSE))
    assert required == {"POSTGRES_PASSWORD"}


def _docker_compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "compose", "version"], capture_output=True, timeout=30
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_compose = pytest.mark.skipif(not _docker_compose_available(), reason="docker compose absent")


@pytest.fixture
def project(tmp_path):
    """A copy of compose.yaml with the one required secret and no other key: no `VEXA_*`."""
    shutil.copy(ROOT / "compose.yaml", tmp_path / "compose.yaml")
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=placeholder\n")
    return tmp_path


def _compose(project: Path, *args: str):
    return subprocess.run(
        ["docker", "compose", *args], cwd=project, capture_output=True, text=True, timeout=60
    )


@needs_compose
def test_config_is_valid_with_a_minimal_env_and_no_vexa_keys(project):
    assert _compose(project, "config", "-q").returncode == 0


@needs_compose
def test_there_are_no_profiles_and_the_services_are_the_apps_own(project):
    profiles = _compose(project, "config", "--profiles")
    assert profiles.returncode == 0 and profiles.stdout.split() == []
    services = _compose(project, "config", "--services")
    assert services.returncode == 0 and set(services.stdout.split()) == APP_SERVICES


@needs_compose
def test_the_networks_and_volumes_are_the_apps_own(project):
    result = _compose(project, "config", "--format", "json")
    assert result.returncode == 0, result.stderr
    import json

    config = json.loads(result.stdout)
    assert set(config["networks"]) == {"default", "front", "appdb"}
    assert set(config["volumes"]) == {"dbdata", "audiodata"}
