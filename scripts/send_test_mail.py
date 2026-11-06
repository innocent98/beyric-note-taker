"""Send ONE real test mail with a PDF and a Word attachment, to an address you give, and print
what Resend answered (spike ML1's one-off check; docs/spike/resend-limits-2026-11-04.md).

    docker compose exec -T web python scripts/send_test_mail.py you@example.com

It uses the server's own RESEND_API_KEY and MAIL_FROM, so it is the real domain and the real
plan. It prints the HTTP status, Resend's `x-resend-daily-quota` / monthly quota headers when
Resend sends them, and the size of each file. Nothing is stored. Run it once, after the W6 mail
code is deployed.
"""

import base64
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import export  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.notes_document import build_notes_document  # noqa: E402

NOTES = "## Summary\nA test of attachments from Beyric Note Taker.\n\n## Decisions\n- None.\n"


def main() -> None:
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        sys.exit(__doc__)
    settings = get_settings()
    if not settings.resend_api_key:
        sys.exit("RESEND_API_KEY is not set here")
    doc = build_notes_document(
        title="Attachment test",
        platform_label="Test",
        started_at=datetime.now(UTC),
        recorded_seconds=60,
        notes=NOTES,
        transcript="[Ada]: Hello.",
        generated_at=datetime.now(UTC),
    )
    files = {"pdf": export.build_file(doc, "pdf"), "docx": export.build_file(doc, "docx")}
    body = {
        "from": settings.mail_from,
        "to": [sys.argv[1]],
        "subject": "Beyric Note Taker: attachment test",
        "text": "This is a test of a PDF and a Word attachment. Nothing to do.",
        "attachments": [
            {"filename": f"test.{ext}", "content": base64.b64encode(data).decode("ascii")}
            for ext, data in files.items()
        ],
    }
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json=body,
        timeout=30,
    )
    print("status:", response.status_code)
    for name, data in files.items():
        print(f"{name}: {len(data)} bytes")
    for header, value in response.headers.items():
        if "quota" in header.lower() or "ratelimit" in header.lower() or "rate-limit" in header:
            print(f"{header}: {value}")
    print("body:", response.text[:300])


if __name__ == "__main__":
    main()
