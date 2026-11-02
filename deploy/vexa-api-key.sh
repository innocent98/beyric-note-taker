#!/usr/bin/env bash
# Create the Vexa user and API key the app uses, and print the key once (vxa_bot_...).
# Run from the project root after `docker compose up -d vexa`. The admin token is read from the
# container's own environment, so it never appears in shell history. Vexa's admin API (port 8001)
# listens only inside the container and can come up after the gateway, so the first call retries.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

admin() {
    docker compose exec -T vexa sh -c \
        'curl -sf -X POST "http://127.0.0.1:8001$1" -H "X-Admin-API-Key: $ADMIN_API_TOKEN" -H "Content-Type: application/json" -d "$2"' \
        _ "$1" "$2"
}

user=""
for _ in $(seq 1 30); do
    if user="$(admin /admin/users '{"email":"notes-app@localhost","name":"Beyric Note Taker","max_concurrent_bots":1}')"; then
        break
    fi
    sleep 4
done
[ -n "$user" ] || { echo "Vexa admin API did not answer" >&2; exit 1; }

user_id="$(printf '%s' "$user" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')"
admin "/admin/users/$user_id/tokens" '{"scopes":["bot","tx"]}' \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])'
