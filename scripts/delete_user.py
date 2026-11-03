"""Delete one account and everything of theirs, now (the same function the admin API uses).

    python scripts/delete_user.py someone@example.com          # asks you to type the address
    python scripts/delete_user.py someone@example.com --yes    # no question

Stops the user's running bots, deletes their recordings here and at Vexa, then their meetings,
templates, vocabulary, settings, notifications, queued mail, tokens and waitlist row, and the user.
The hours ledger keeps seconds and an email hash only, so signing up again does not reset the
hours. Refuses to delete the last active admin. Run it where the app's `.env` is, for example:

    docker compose exec web python scripts/delete_user.py someone@example.com
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import admin, deletion, signup  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Meeting, User  # noqa: E402
from app.vexa import VexaClient, VexaError  # noqa: E402


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args(argv)
    address = signup.normalise_email(args.email)
    with SessionLocal() as session:
        user = (
            session.scalars(select(User).where(User.email == address)).first() if address else None
        )
        if user is None:
            print(f"No account with that email address.", file=sys.stderr)  # noqa: F541
            return 1
        if user.is_admin and user.status == "active":
            others = session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.is_admin.is_(True), User.status == "active", User.id != user.id)
            )
            if others == 0:
                print("Refusing: this is the last admin account.", file=sys.stderr)
                return 1
        meetings = session.scalar(
            select(func.count()).select_from(Meeting).where(Meeting.owner_user_id == user.id)
        )
        print(f"Account {user.email}: {meetings} meetings will be deleted, for good.")
        if (
            not args.yes
            and input(f"Type the address ({user.email}) to confirm: ").strip() != user.email
        ):
            print("Not confirmed; nothing was deleted.", file=sys.stderr)
            return 1
        # The decisive last-admin check, under the admin lock and after the question (the lock is
        # held until the purge commits, so it is never held while waiting for a person to type).
        if admin.last_admin_locked(session, user):
            session.rollback()
            print("Refusing: this is the last admin account.", file=sys.stderr)
            return 1
        try:
            deletion.purge_user(session, user, VexaClient())
        except VexaError as exc:
            print(
                f"Vexa did not answer ({type(exc).__name__}); nothing was deleted.", file=sys.stderr
            )
            return 1
    print("Deleted.")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
