"""Logs in to the JSON API once and reports what the known-device cookie looks like, or logs in
sending a device cookie. Runs in a container on the e2e network.

  device_check.py BASE PASSWORD                 # login; prints "STATUS <device cookie value>" and
                                                # the Set-Cookie attributes with the value hidden
  device_check.py BASE PASSWORD DEVICE_VALUE    # login carrying that device cookie; prints STATUS
"""

import json
import sys
import urllib.error
import urllib.request

OWNER_EMAIL = "owner@example.test"  # the account the e2e stack's migration creates

base, password = sys.argv[1:3]
device = sys.argv[3] if len(sys.argv) > 3 else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(NoRedirect)
start = opener.open(urllib.request.Request(base + "/api/session"))
cookie = start.headers["Set-Cookie"].split(";")[0]
token = json.loads(start.read())["csrf_token"]
headers = {"Cookie": cookie, "X-CSRF-Token": token, "Content-Type": "application/json"}
if device:
    headers["Cookie"] += f"; device={device}"
request = urllib.request.Request(
    base + "/api/session",
    data=json.dumps({"email": OWNER_EMAIL, "password": password}).encode(),
    headers=headers,
)
try:
    response = opener.open(request)
except urllib.error.HTTPError as error:
    response = error
code = getattr(response, "status", None) or response.code
set_cookies = response.headers.get_all("Set-Cookie") or []
value = ""
for line in set_cookies:
    if line.startswith("device="):
        value = line.split(";")[0].split("=", 1)[1]
        print("set-cookie: device=<signed-value>;" + line.split(";", 1)[1])
print(code, value)
