"""Write the JSON API's OpenAPI schema to `docs/openapi.json` for the frontend's type generator.

    python scripts/export_openapi.py            # rewrite docs/openapi.json
    python scripts/export_openapi.py --check    # exit 1 if the committed file is out of date

The running app keeps `/openapi.json` and the docs pages switched off; this builds the schema
from the API router directly.
"""

import json
import sys
from pathlib import Path

from fastapi.openapi.utils import get_openapi

from app import api

TARGET = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
DESCRIPTION = """\
Owner-only JSON API for the Beyric Note Taker frontend. Same origin as the app.

Auth is the signed `session` cookie. Call `GET /api/session` first: it returns `csrf_token`,
which every call other than GET must send in the `X-CSRF-Token` header. Every error has the
shape `{"error": {"code", "message"}}` (validation errors add `fields`).
"""


def build() -> dict:
    return get_openapi(
        title="Beyric Note Taker API",
        version="1",
        description=DESCRIPTION,
        routes=api.router.routes,  # the pages are not an API, so they are left out
    )


def main() -> int:
    text = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if TARGET.read_text() != text:
            print(f"{TARGET} is out of date: run python scripts/export_openapi.py", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(text)
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
