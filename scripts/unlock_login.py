"""End a login lockout on one account (the failed-login counters kept in the database).

    python scripts/unlock_login.py someone@example.com

Ten wrong passwords an hour from two or more addresses lock an account against logins from a
device it has not signed in from before; the lock now outlives a restart. A successful password
reset ends it by itself; this is for the operator, who cannot look the rows up (they are keyed
hashes, not addresses). It clears that account's counter only: not the address or device counters
of whoever was guessing, and no other account. Run it where the app's `.env` is, for example:

    docker compose exec web python scripts/unlock_login.py someone@example.com
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, ratelimit, signup  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def unlock(store: ratelimit.Store, email: str) -> int:
    """Forget the failed logins counted against `email`; how many hits were forgotten."""
    return store.clear("login_account", auth.account_key(email))


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    args = parser.parse_args(argv)
    address = signup.normalise_email(args.email)
    if not address:
        print("That is not an email address.", file=sys.stderr)
        return 1
    store = ratelimit.DbStore(SessionLocal, ratelimit.store_secret(get_settings()))
    cleared = unlock(store, address)
    print(
        f"Cleared {cleared} recorded failed login(s) for that account."
        if cleared
        else "Nothing was recorded for that account."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
