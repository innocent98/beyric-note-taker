"""Tiny HTTP client run inside a container on the e2e network (stdlib only).

  client.py BASE COUNT [--xff-from N] [--secret S] [--password P]

Sends COUNT login attempts to the JSON API and prints the status codes. Each attempt fetches a
session (for the cookie and CSRF token) and POSTs the password. --xff-from N makes attempt i claim X-Forwarded-For
198.51.100.(N+i); --secret sends X-Edge-Secret.
"""

import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

OWNER_EMAIL = "owner@example.test"  # the account the e2e stack's migration creates


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    args = sys.argv[1:]
    base, count = args[0], int(args[1])
    opts = dict(zip(args[2::2], args[3::2]))
    password = opts.get("--password") or secrets.token_hex(8)  # a random wrong guess
    opener = urllib.request.build_opener(NoRedirect)
    codes = []
    for i in range(count):
        headers = {}
        if "--xff-from" in opts:
            headers["X-Forwarded-For"] = f"198.51.100.{int(opts['--xff-from']) + i}"
        if "--secret" in opts:
            headers["X-Edge-Secret"] = opts["--secret"]
        # The JSON API, as the Next.js front end uses it: GET gives a cookie and CSRF token.
        page = opener.open(urllib.request.Request(base + "/api/session", headers=headers))
        cookie = page.headers["Set-Cookie"].split(";")[0]
        token = json.loads(page.read())["csrf_token"]
        request = urllib.request.Request(
            base + "/api/session",
            data=json.dumps({"email": OWNER_EMAIL, "password": password}).encode(),
            headers={
                **headers,
                "Cookie": cookie,
                "X-CSRF-Token": token,
                "Content-Type": "application/json",
            },
        )
        try:
            codes.append(opener.open(request).status)
        except urllib.error.HTTPError as error:
            codes.append(error.code)
    print(",".join(map(str, codes)))


main()
