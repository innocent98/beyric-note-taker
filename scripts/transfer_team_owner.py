"""Hand a team to another of its members (the pilot admin does this by hand: spec section 4).

    python scripts/transfer_team_owner.py <team id> <new owner email>
    docker compose exec web python scripts/transfer_team_owner.py 12 dan@example.com

The new owner must already be a member of the team. The old owner stays on the team as an admin.
Needed before an account that owns a team with other members can be deleted.
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import signup, teams  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("team_id", type=int)
    parser.add_argument("email")
    args = parser.parse_args(argv)
    address = signup.normalise_email(args.email)
    with SessionLocal() as session:
        user = (
            session.scalars(select(User).where(User.email == address)).first() if address else None
        )
        if user is None or user.status != "active":
            print("No active account with that email address.", file=sys.stderr)
            return 1
        try:
            teams.transfer_owner(session, args.team_id, user.id)
        except teams.TeamChangeRefused as exc:
            session.rollback()
            print(exc.message, file=sys.stderr)
            return 1
        session.commit()
    print("Done: the old owner is now an admin of that team.")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
