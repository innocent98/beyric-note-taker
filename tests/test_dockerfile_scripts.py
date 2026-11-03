"""The operator scripts the docs run inside the `web` container must be in the image.

On 2026-11-03 `docker compose exec web python scripts/backfill_extraction.py` failed on the server
("can't open file '/srv/scripts/...'") because the Dockerfile copied `app/` and `alembic/` only.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCUMENTED = re.compile(r"docker compose exec(?: -T)? (?:web|worker) python scripts/(\w+\.py)")


def test_the_image_copies_the_scripts_directory():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^COPY scripts \./scripts$", dockerfile, re.MULTILINE)


def test_every_script_the_docs_run_in_a_container_exists():
    seen = set()
    for path in [*(ROOT / "docs" / "sop").glob("*.md"), ROOT / "deploy" / "README.md"]:
        seen |= set(DOCUMENTED.findall(path.read_text()))
    assert seen, "no documented container script commands found: the pattern is stale"
    missing = [name for name in sorted(seen) if not (ROOT / "scripts" / name).is_file()]
    assert not missing
