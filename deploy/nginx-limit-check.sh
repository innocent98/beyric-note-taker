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
set -euo pipefail

conf="${1:-$(dirname "$0")/nginx.conf}"
expect_bug=false
[ "${2:-}" = "--expect-bug" ] && expect_bug=true
work="$(mktemp -d)"
net="nginx-limit-check-$$"
trap 'docker rm -f "$net-up" "$net-nginx" >/dev/null 2>&1 || true; docker network rm "$net" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT

# The config proxies to 127.0.0.1:8020; point it at the upstream container instead.
sed "s#http://127.0.0.1:8020#http://$net-up:8020#" "$conf" > "$work/site.conf"
printf 'server { listen 8020; location / { return 200 ok; } }\n' > "$work/upstream.conf"

port="${CHECK_PORT:-18099}"
docker network create "$net" >/dev/null
docker run -d --name "$net-up" --network "$net" -v "$work/upstream.conf:/etc/nginx/conf.d/default.conf:ro" nginx:alpine >/dev/null
docker run -d --name "$net-nginx" --network "$net" -p "127.0.0.1:$port:80" -v "$work/site.conf:/etc/nginx/conf.d/site.conf:ro" \
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

exit $fail
