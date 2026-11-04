"""Stop mail to an address, allow it again, or ask whether it is stopped (`mail_suppressions`).

    python scripts/suppress_email.py add someone@example.com bounce
    python scripts/suppress_email.py add someone@example.com complaint
    python scripts/suppress_email.py add someone@example.com unsubscribed
    python scripts/suppress_email.py check someone@example.com
    python scripts/suppress_email.py remove someone@example.com

Use `bounce` or `complaint` after Resend's dashboard shows one (they stop all mail to the address,
sign-in mail included); `unsubscribed` stops everything except sign-in mail. Only a hash of the
address is stored. The sender looks at the table when it sends, so a queued mail is stopped too.
There is no webhook yet: this is how a bounce or complaint reaches the table. Run it where the
app's `.env` is, for example:

    docker compose exec web python scripts/suppress_email.py add someone@example.com bounce
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import mail, signup  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import SUPPRESSION_REASONS  # noqa: E402


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("email")
    add.add_argument("reason", choices=SUPPRESSION_REASONS)
    for name in ("check", "remove"):
        commands.add_parser(name).add_argument("email")
    args = parser.parse_args(argv)
    address = signup.normalise_email(args.email)
    if not address:
        print("That is not an email address.", file=sys.stderr)
        return 1
    with SessionLocal() as session:
        if args.command == "add":
            mail.suppress(session, address, args.reason)
            session.commit()
            print(f"No more mail to that address ({args.reason}), unless it was already stopped.")
        elif args.command == "remove":
            removed = mail.lift_suppression(session, address)
            session.commit()
            print("Mail to that address is allowed again." if removed else "It was not stopped.")
        else:
            reason = mail.suppression_reason(session, address)
            print(f"Stopped: {reason}." if reason else "Not stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
