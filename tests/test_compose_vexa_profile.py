"""compose.yaml keeps every Vexa service in the `vexa` profile, so Vexa can be stopped for good.

A plain `docker compose up -d` (what the deploy runs) must neither start Vexa nor wait for it, and
nothing outside the profile may depend on a service inside it: compose refuses to start a service
whose dependency is in a profile that is off. The first group reads the file as text, like the
other compose tests; the second asks `docker compose config` (skipped where Docker is absent) and
checks the two service lists differ exactly as intended.
See docs/sop/2026-11-09-vexa-compose-profile.md.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
COMPOSE = (ROOT / "compose.yaml").read_text()
VEXA_SERVICES = {"vexa-db", "vexa-storage", "vexa-storage-init", "vexa", "vexa-schema-fix"}
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


def test_the_services_are_the_ones_this_test_knows():
    assert set(_services()) == VEXA_SERVICES | APP_SERVICES


@pytest.mark.parametrize("name", sorted(VEXA_SERVICES))
def test_every_vexa_service_is_in_the_vexa_profile(name):
    assert re.search(r'^    profiles: \["vexa"\]$', _services()[name], re.M)


@pytest.mark.parametrize("name", sorted(APP_SERVICES))
def test_no_app_service_is_in_a_profile(name):
    # A profile on any of these would stop a plain `up -d` from starting the site.
    assert "profiles:" not in _services()[name]


@pytest.mark.parametrize("name", sorted(APP_SERVICES))
def test_no_app_service_depends_on_a_vexa_service(name):
    block = _services()[name]
    assert not _depends_on(block) & VEXA_SERVICES
    # Not even by a stray `depends_on` written in the short list form.
    assert not re.search(r"depends_on:\s*\[[^\]]*vexa", block)


@pytest.mark.parametrize("name", sorted(VEXA_SERVICES))
def test_a_vexa_service_depends_only_on_other_vexa_services(name):
    assert _depends_on(_services()[name]) <= VEXA_SERVICES


def test_the_vexa_volumes_and_env_file_are_still_declared():
    # Stopping Vexa must not delete anything; bringing it back needs both again.
    assert re.search(r"^  vexapgdata:\s*$", COMPOSE, re.M)
    assert re.search(r"^  vexastorage:\s*$", COMPOSE, re.M)
    assert "env_file: .env.vexa" in _services()["vexa"]
    assert "vexapgdata:/var/lib/postgresql/data" in _services()["vexa-db"]
    assert "vexastorage:/srv/vexa-storage" in _services()["vexa-storage"]


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
    """A copy of compose.yaml with placeholder secrets: `config` needs `.env` (the required
    variables are checked even for a service whose profile is off) and `.env.vexa` (read only when
    the profile is on)."""
    shutil.copy(ROOT / "compose.yaml", tmp_path / "compose.yaml")
    (tmp_path / ".env").write_text(
        "POSTGRES_PASSWORD=placeholder\nVEXA_DB_PASSWORD=placeholder\nVEXA_STORAGE_SECRET=placeholder\n"
    )
    (tmp_path / ".env.vexa").write_text("")
    return tmp_path


def _compose(project: Path, *args: str, profiles: str | None = None):
    env = {k: v for k, v in os.environ.items() if k != "COMPOSE_PROFILES"}
    if profiles is not None:
        env["COMPOSE_PROFILES"] = profiles
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@needs_compose
def test_compose_config_is_valid_in_both_modes(project):
    assert _compose(project, "config", "-q").returncode == 0
    assert _compose(project, "config", "-q", profiles="vexa").returncode == 0


@needs_compose
def test_the_only_profile_is_vexa(project):
    result = _compose(project, "config", "--profiles")
    assert result.returncode == 0 and result.stdout.split() == ["vexa"]


@needs_compose
def test_the_service_lists_differ_exactly_by_the_vexa_services(project):
    off = _compose(project, "config", "--services")
    on = _compose(project, "config", "--services", profiles="vexa")
    assert off.returncode == 0 and on.returncode == 0
    assert set(off.stdout.split()) == APP_SERVICES
    assert set(on.stdout.split()) == APP_SERVICES | VEXA_SERVICES


@needs_compose
def test_naming_a_vexa_service_turns_its_profile_on(project):
    # `docker compose up -d vexa` (README section 4 and the image upgrade) keeps working.
    result = _compose(project, "up", "--dry-run", "vexa")
    assert result.returncode == 0, result.stderr
    assert "vexa-db" in result.stderr and "vexa-storage" in result.stderr
