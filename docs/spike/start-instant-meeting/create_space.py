"""Create a Google Meet through the Meet REST API and print its link.

Run:  python3 create_space.py                      (Google's default access for your account)
      python3 create_space.py --access open        (ask for "anyone with the link can join")
      python3 create_space.py --access trusted | restricted
      python3 create_space.py --get spaces/XXXX    (read an existing space back)

Prints the link, the access type you ASKED for and the one Google reports BACK. If those differ,
Google changed it, which is a finding. A refusal prints Google's own words.
"""

from __future__ import annotations

import argparse
import json

import instant_common as ic


def call(method: str, url: str, token: str, payload: dict | None = None) -> ic.Space:
    body = json.dumps(payload).encode() if payload is not None else None
    status, text = ic.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        body=body,
    )
    if status != 200:
        raise ic.SpikeError(ic.explain_error(status, text))
    try:
        return ic.parse_space(json.loads(text))
    except ValueError as exc:
        raise ic.SpikeError(f"Google said OK but the answer is not a meeting space: {exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--access", choices=sorted(ic.ACCESS_TYPES), default="default")
    parser.add_argument("--get", metavar="SPACE_NAME", help="read back spaces/XXXX instead")
    args = parser.parse_args()
    token = ic.access_token()

    if args.get:
        space = call("GET", f"https://meet.googleapis.com/v2/{args.get}", token)
        ic.log_space(space, "n/a", "get")
        print(f"Read back {space.name}: access type is {space.access_type}")
        return

    asked = ic.ACCESS_TYPES[args.access]
    payload = {"config": {"accessType": asked}} if asked else {}
    space = call("POST", ic.SPACES_URL, token, payload)
    ic.log_space(space, asked or "default", "create")

    print("Google CREATED a meeting for this account.")
    print(f"  Link to open and join as host : {space.meeting_uri}")
    print(f"  Space name                    : {space.name}")
    print(f"  Access type you asked for     : {asked or '(nothing: Google default)'}")
    print(f"  Access type Google reports    : {space.access_type}")
    print(f"  Entry points                  : {space.entry_point_access}")
    if asked and space.access_type != asked:
        print("  NOTE: Google did NOT apply the access type you asked for. Record both lines.")
    if space.access_type == "OPEN":
        print("  OPEN means anyone with the link can join without knocking.")
    elif space.access_type in ("TRUSTED", "RESTRICTED"):
        print("  This setting makes a guest with no invitation knock and wait to be admitted.")
    print("\nNext: open the link signed in as the SAME Google account and click Join now.")
    print(f"Then:  python3 send_bot.py {space.meeting_code}")


if __name__ == "__main__":
    try:
        main()
    except ic.SpikeError as exc:
        raise SystemExit(f"\nSTOP: {exc}") from None
