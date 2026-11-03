#!/usr/bin/env bash
# End to end proof of the edge secret on the real stack shape: nginx (deploy/nginx.conf) -> next ->
# web (FastAPI) -> db, in a throwaway compose project. Needs Docker; takes a few minutes the first
# time (it builds the two images). Output of the run that backs the SOP: e2e-output.txt.
#
#   docs/spike/edge-secret/e2e.sh | tee docs/spike/edge-secret/e2e-output.txt
#
# The network is 172.28.1.0/24 with next at 172.28.1.10, as in compose.yaml, because the app
# recognises next by that address. Visitors are containers with fixed addresses on it, so nginx sees a different
# $remote_addr for each. The "hostile" container is any other container on the host: it talks to
# `next` (or `web`) directly, skipping nginx, and forges X-Forwarded-For.
# All passwords and secrets here are throwaway values generated for this run.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"; repo="$here/../../.."
export E2E_WORK="$(mktemp -d)"
export E2E_OWNER_PASSWORD="$(openssl rand -hex 16)"
export E2E_DB_PASSWORD="$(openssl rand -hex 12)"
export E2E_SESSION_SECRET="$(openssl rand -hex 32)"
export E2E_EDGE_SECRET="$(openssl rand -hex 32)"
compose=(docker compose -f "$here/compose.e2e.yaml")
trap '"${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; rm -rf "$E2E_WORK"' EXIT

# The real site file, pointed at the next container, without the edge login limit (so the app's own
# limits are what answers; the edge limit is proven by deploy/nginx-limit-check.sh).
sed -e "s#http://127.0.0.1:8020#http://172.28.1.10:3000#" \
    -e '/limit_req zone=notes_login/d' "$repo/deploy/nginx.conf" > "$E2E_WORK/site.conf"
printf 'proxy_set_header X-Edge-Secret "%s";\n' "$E2E_EDGE_SECRET" > "$E2E_WORK/edge-secret.conf"

client() {  # ip, then the arguments of client.py
  local ip="$1"; shift
  docker run --rm --network edgee2e_front --ip "$ip" -v "$here/client.py:/client.py:ro" \
    --entrypoint python edgee2e-web /client.py "$@" || echo "client error"
}
NGINX=http://172.28.1.5; NEXT=http://172.28.1.10:3000; WEB=http://172.28.1.20:8000
wait_ready() {
  for _ in $(seq 1 60); do
    docker run --rm --network edgee2e_front --entrypoint python edgee2e-web \
      -c "import urllib.request as u; u.urlopen('$NGINX/health', timeout=3)" 2>/dev/null && return 0
    sleep 1
  done; echo "stack did not come up" >&2; return 1
}
reset() { "${compose[@]}" up -d --force-recreate web nginx >/dev/null 2>&1; wait_ready; }  # clears the in-memory counters

fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS  $1: $3"; else echo "FAIL  $1: got $3, wanted $2"; fail=1; fi; }

echo "== build and start =="
"${compose[@]}" up -d --build 2>&1 | tail -3
wait_ready

echo
echo "== A. EDGE_SHARED_SECRET set in the app and in nginx =="
reset
check "visitor 1, six wrong passwords through nginx" "401,401,401,401,401,429" "$(client 172.28.1.101 $NGINX 6)"
check "visitor 2 is not affected (own address reaches the app)" "401" "$(client 172.28.1.102 $NGINX 1)"

reset
check "forged X-Forwarded-For and X-Edge-Secret sent to nginx change nothing" \
  "401,401,401,401,401,429" "$(client 172.28.1.101 $NGINX 6 --xff-from 10 --secret attacker-guess)"
check "visitor 2 still has its own count" "401" "$(client 172.28.1.102 $NGINX 1)"

reset
check "hostile container -> next, a new forged address each time, no secret" \
  "401,401,401,401,401,429" "$(client 172.28.1.103 $NEXT 6 --xff-from 1)"
reset
check "hostile container -> next with a wrong secret" \
  "401,401,401,401,401,429" "$(client 172.28.1.103 $NEXT 6 --xff-from 1 --secret not-the-secret)"
reset
check "hostile container -> web directly (the rollback port's shape)" \
  "401,401,401,401,401,429" "$(client 172.28.1.103 $WEB 6 --xff-from 1)"

reset
echo "-- account limit: three visitors, four wrong passwords each (10 allowed per account per hour)"
a="$(client 172.28.1.101 $NGINX 4)"; b="$(client 172.28.1.102 $NGINX 4)"; c="$(client 172.28.1.103 $NGINX 4)"
check "visitors 1 and 2 are all 401; visitor 3 runs into the account limit" \
  "401,401,401,401|401,401,401,401|401,401,429,429" "$a|$b|$c"
check "a fresh visitor with the RIGHT password is refused too" "429" \
  "$(client 172.28.1.104 $NGINX 1 --password "$E2E_OWNER_PASSWORD")"
reset
check "after a restart the owner can log in through nginx" "200" \
  "$(client 172.28.1.104 $NGINX 1 --password "$E2E_OWNER_PASSWORD")"

echo "-- known device: strangers' failures lock the account, the owner's browser still gets in"
reset
dev() {  # ip, then the arguments of device_check.py
  local ip="$1"; shift
  docker run --rm --network edgee2e_front --ip "$ip" -v "$here/device_check.py:/device_check.py:ro" \
    --entrypoint python edgee2e-web /device_check.py "$NGINX" "$@" || echo "client error"
}
check "a wrong password gets 401 and no device cookie" "401 " "$(dev 172.28.1.109 wrong-password-guess | tail -1)"
first="$(dev 172.28.1.106 "$E2E_OWNER_PASSWORD")"   # success also clears the account's failures
printf '%s\n' "$first" | sed -n '1p'
status_and_cookie="$(printf '%s\n' "$first" | tail -1)"
check "owner logs in and gets a device cookie" "200" "${status_and_cookie%% *}"
device_value="${status_and_cookie#* }"
a="$(client 172.28.1.101 $NGINX 4)"; b="$(client 172.28.1.102 $NGINX 4)"; c="$(client 172.28.1.103 $NGINX 4)"
check "strangers lock the account (10 failures)" "401,401,401,401|401,401,401,401|401,401,429,429" "$a|$b|$c"
check "a stranger with the right password is refused" "429 " "$(dev 172.28.1.107 "$E2E_OWNER_PASSWORD" | tail -1)"
check "a forged device cookie gets no bypass" "429" \
  "$(dev 172.28.1.108 "$E2E_OWNER_PASSWORD" "forged.cookie" | tail -1 | cut -d' ' -f1)"
check "the owner's browser with the device cookie is let in" "200" \
  "$(dev 172.28.1.106 "$E2E_OWNER_PASSWORD" "$device_value" | tail -1 | cut -d' ' -f1)"
check "X-Edge-Ok through nginx" "true" "$(docker run --rm --network edgee2e_front --entrypoint python edgee2e-web -c "
import urllib.request as u
print(u.urlopen('$NGINX/health').headers['X-Edge-Ok'])")"
check "X-Edge-Ok straight at next (no secret)" "false" "$(docker run --rm --network edgee2e_front --entrypoint python edgee2e-web -c "
import urllib.request as u
print(u.urlopen('$NEXT/health').headers['X-Edge-Ok'])")"
client 172.28.1.103 $NEXT 1 >/dev/null   # a request through next that skips nginx
check "next without the secret is noticed in the app log" "1" \
  "$("${compose[@]}" logs web 2>&1 | grep -c 'arrived without a valid X-Edge-Secret' || true)"

echo "-- live captures of the JSON login limit for the FE guide (docs/fe-captures/owner-app/)"
reset
docker run --rm --network edgee2e_front --ip 172.28.1.105 \
  -v "$here/capture_limits.py:/capture_limits.py:ro" -v "$repo/docs/fe-captures/owner-app:/out" \
  --entrypoint python edgee2e-web /capture_limits.py "$NGINX" /out "$E2E_OWNER_PASSWORD"

echo "-- the secret never reaches the browser"
leak="$(docker run --rm --network edgee2e_front --entrypoint python edgee2e-web -c "
import urllib.request as u
for path in ('/', '/login', '/privacy', '/api/session'):
    r = u.urlopen('$NGINX' + path)
    print(r.headers, r.read().decode())
" | grep -c "$E2E_EDGE_SECRET" || true)"
check "responses through nginx contain no copy of the secret" "0" "$leak"
check "no startup warning with the secret set" "0" \
  "$("${compose[@]}" logs web 2>&1 | grep -c 'EDGE_SHARED_SECRET is not set' || true)"

echo
echo "== B. secret NOT set in the app (the state of the server until the operator sets it) =="
export E2E_EDGE_SECRET_BACKUP="$E2E_EDGE_SECRET"; export E2E_EDGE_SECRET=""
export E2E_TRUSTED_PROXY_IPS="172.28.0.1"   # the server's TRUSTED_PROXY_IPS (next, 172.28.1.10, is always trusted)
reset
"${compose[@]}" logs web 2>&1 | grep -m1 'EDGE_SHARED_SECRET is not set' | sed 's/^[^|]*| //'
check "startup warning is logged" "1" \
  "$("${compose[@]}" logs web 2>&1 | grep -c 'EDGE_SHARED_SECRET is not set' || true)"
check "as before: visitor 1 limited through nginx" "401,401,401,401,401,429" "$(client 172.28.1.101 $NGINX 6)"
check "as before: visitor 2 not affected" "401" "$(client 172.28.1.102 $NGINX 1)"
reset
check "as before (the gap the secret closes): a hostile container's forged addresses are believed, so it is never limited" \
  "401,401,401,401,401,401" "$(client 172.28.1.103 $NEXT 6 --xff-from 1)"

exit $fail
