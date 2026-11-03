#!/usr/bin/env bash
# Proves nginx.conf's login limit also counts POST /login/ and /api/session/ (the Next.js front door
# drops the trailing slash, so those reach the app as /login and /api/session). Runs nginx from
# the given config in a throwaway container with a second nginx as the upstream; needs Docker.
#
#   deploy/nginx-limit-check.sh [path/to/nginx.conf] [--expect-bug]
#
# With the shipped config every POST beyond the allowance (1 + burst 3) is 429, whichever spelling
# is used, and the spellings share one counter. `--expect-bug` inverts the check, for running an
# old config as a control (the trailing-slash spellings must then be uncounted).
#
# It also proves the edge secret: every proxied request carries the configured X-Edge-Secret and
# the visitor's address in X-Forwarded-For, and a client-sent copy of either is replaced, not
# passed on or appended to. The secret here is a dummy written to the file nginx.conf includes.
set -euo pipefail

conf="${1:-$(dirname "$0")/nginx.conf}"
expect_bug=false
[ "${2:-}" = "--expect-bug" ] && expect_bug=true
work="$(mktemp -d)"
net="nginx-limit-check-$$"
trap 'docker rm -f "$net-up" "$net-nginx" >/dev/null 2>&1 || true; docker network rm "$net" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT

# The config proxies to 127.0.0.1:8020; point it at the upstream container instead.
sed "s#http://127.0.0.1:8020#http://$net-up:8020#" "$conf" > "$work/site.conf"
# The upstream answers with the two headers the app trusts, so the check can read them back.
printf 'server { listen 8020; location / { return 200 "$http_x_edge_secret|$http_x_forwarded_for"; } }\n' > "$work/upstream.conf"
printf 'proxy_set_header X-Edge-Secret "dummy-secret-for-the-check";\n' > "$work/edge-secret.conf"

port="${CHECK_PORT:-18099}"
docker network create "$net" >/dev/null
docker run -d --name "$net-up" --network "$net" -v "$work/upstream.conf:/etc/nginx/conf.d/default.conf:ro" nginx:alpine >/dev/null
docker run -d --name "$net-nginx" --network "$net" -p "127.0.0.1:$port:80" -v "$work/site.conf:/etc/nginx/conf.d/site.conf:ro" \
  -v "$work/edge-secret.conf:/etc/nginx/snippets/beyric-edge-secret.conf:ro" \
  nginx:alpine sh -c "rm -f /etc/nginx/conf.d/default.conf; nginx -g 'daemon off;'" >/dev/null
wait_ready() { for _ in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$port/" -H 'Host: notes.beyrictech.com' && return 0; sleep 0.5; done; echo "nginx did not start" >&2; return 1; }
wait_ready

post() { curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Host: notes.beyrictech.com' "http://127.0.0.1:$port$1"; }
restart() { docker restart "$net-nginx" >/dev/null; wait_ready; }   # resets the limit counters

fail=0
check() {  # label, expected (space separated), actual
  if [ "$2" = "$3" ]; then echo "PASS  $1: $3"; else echo "FAIL  $1: got $3, wanted $2"; fail=1; fi
}

# One spelling: 4 allowed (1 + burst 3), then 429.
for path in /login /login/ /api/session /api/session/; do
  restart
  got=""; for _ in 1 2 3 4 5 6; do got="$got $(post "$path")"; done
  if $expect_bug && [[ "$path" == */ ]]; then want=" 200 200 200 200 200 200"; else want=" 200 200 200 200 429 429"; fi
  check "POST $path x6" "$want" "$got"
done

# The spellings share one counter per address.
restart
got="$(post /login) $(post /login/) $(post /api/session) $(post /api/session/) $(post /login/)"
if $expect_bug; then want="200 200 200 200 200"; else want="200 200 200 200 429"; fi
check "mixed spellings share the limit" "$want" "$got"

# Page views are never counted.
restart
got=""; for _ in 1 2 3 4 5 6; do got="$got $(curl -s -o /dev/null -w '%{http_code}' -H 'Host: notes.beyrictech.com' "http://127.0.0.1:$port/login/")"; done
check "GET /login/ x6 is not counted" " 200 200 200 200 200 200" "$got"

# The edge secret is set by nginx; a client cannot supply its own or choose the forwarded address.
restart
got="$(curl -s -H 'Host: notes.beyrictech.com' -H 'X-Edge-Secret: attacker' -H 'X-Forwarded-For: 203.0.113.66' "http://127.0.0.1:$port/login")"
if [[ "$got" == "dummy-secret-for-the-check|"* ]]; then ok=ok; else ok="$got"; fi
check "client X-Edge-Secret is replaced by nginx's" ok "$ok"
# One address (nginx's own view of the client), not the one the client claimed, not a list.
if [[ "$got" =~ ^dummy-secret-for-the-check\|[0-9a-f.:]+$ && "$got" != *203.0.113.66* ]]; then ok=ok; else ok="$got"; fi
check "client X-Forwarded-For is replaced by the peer address" ok "$ok"

# The public account routes (see the comment in nginx.conf): one shared counter of 1 + burst 10,
# counted for every method, and the waitlist's own of 1 + burst 5.
get() { curl -s -o /dev/null -w '%{http_code}' -H 'Host: notes.beyrictech.com' "http://127.0.0.1:$port$1"; }
auth_paths="/api/signup /api/signup/resend /api/verify-email /api/password/forgot /api/password/reset"
for path in $auth_paths /api/auth/google/start /api/signup/; do
  restart
  got=""; for _ in $(seq 1 13); do got="$got $(post "$path")"; done
  check "POST $path x13" " 200 200 200 200 200 200 200 200 200 200 200 429 429" "$got"
done
restart
got=""; for _ in $(seq 1 13); do got="$got $(get /api/auth/google/start)"; done
check "GET /api/auth/google/start x13 is counted" " 200 200 200 200 200 200 200 200 200 200 200 429 429" "$got"
restart
got=""
for path in /api/signup /api/verify-email /api/password/forgot /api/signup/resend /api/password/reset; do
  for _ in 1 2 3; do got="$got $(post "$path")"; done
done
check "the account routes share one counter" " 200 200 200 200 200 200 200 200 200 200 200 429 429 429 429" "$got"
check "the waitlist has its own counter" " 200" " $(post /api/waitlist)"
restart
got=""; for _ in $(seq 1 8); do got="$got $(post /api/waitlist)"; done
check "POST /api/waitlist x8" " 200 200 200 200 200 200 429 429" "$got"
restart
got=""; for _ in $(seq 1 13); do got="$got $(get /api/auth/google/callback)"; done
check "the Google callback is not in the account zone" " 200 200 200 200 200 200 200 200 200 200 200 200 200" "$got"
restart
got=""; for _ in $(seq 1 13); do got="$got $(get /api/pilot)"; done
check "GET /api/pilot is not counted" " 200 200 200 200 200 200 200 200 200 200 200 200 200" "$got"

exit $fail
