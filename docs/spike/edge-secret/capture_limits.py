"""Captures the JSON login limit (attempts 5, 6 and a right-password attempt while locked) from the
running e2e stack, in the format of docs/fe-captures/owner-app/. Runs inside a container on the
e2e network; writes to the directory given as the second argument.

  capture_limits.py BASE OUTDIR OWNER_PASSWORD
"""

import json
import sys
import urllib.error
import urllib.request

OWNER_EMAIL = "owner@example.test"  # the account the e2e stack's migration creates

base, outdir, owner_password = sys.argv[1:4]


def call(method, path, body=None, csrf=None, cookie=None):
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    if cookie:
        headers["Cookie"] = cookie
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(request)
    except urllib.error.HTTPError as error:
        response = error
    return response


start = call("GET", "/api/session")
cookie = start.headers["Set-Cookie"].split(";")[0]
csrf = json.loads(start.read())["csrf_token"]


def attempt(name, password, note=""):
    shown = "nope" if password == "nope" else "capture-password-placeholder"
    response = call(
        "POST", "/api/session", {"email": OWNER_EMAIL, "password": password}, csrf, cookie
    )
    capture = {
        "note": note,
        "request": {
            "method": "POST",
            "path": "/api/session",
            "headers": {"X-CSRF-Token": "<csrf-E>", "Cookie": "session=<cookie>"},
            "body": {"email": OWNER_EMAIL, "password": shown},
        },
        "response": {
            "status": response.status if hasattr(response, "status") else response.code,
            "headers": {
                "content-type": response.headers["content-type"],
                "cache-control": response.headers["cache-control"],
            },
            "body": json.loads(response.read()),
        },
    }
    with open(f"{outdir}/{name}.json", "w") as out:
        json.dump(capture, out, indent=2)
        out.write("\n")
    print(name, capture["response"]["status"], capture["response"]["body"])


for _ in range(4):
    call("POST", "/api/session", {"email": OWNER_EMAIL, "password": "nope"}, csrf, cookie)
attempt("79-limit-wrong-password-5", "nope")
attempt("80-limit-sixth-attempt", "nope")
attempt(
    "81-limit-right-password-while-locked",
    owner_password,
    "While locked out even the right password gets 429.",
)
