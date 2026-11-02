# Deploying Beyric Note Taker

One Ubuntu 24.04 server runs everything:

| Piece | Where it runs | Reachable from |
|---|---|---|
| Vexa Lite (bots, live transcription) | Docker, started from a Vexa checkout | host only, in practice (see step 2) |
| App: `db`, `migrate`, `worker`, `web` | Docker Compose in this repo | `web` on `127.0.0.1:8000` only |
| nginx + certbot | **on the host**, not in Docker | the internet, ports 80 and 443 |

nginx runs on the host so certbot can edit nothing and renew with a plain `certbot renew`. The `web` container is published on `127.0.0.1:8000`; nginx connects to it, so the app sees nginx as the Docker bridge gateway `172.28.0.1` (`compose.yaml` pins that network). That is the one trusted proxy.

`web` is published on `127.0.0.1` only (`compose.yaml`: `127.0.0.1:8000:8000`), so only processes on the server itself can open a connection to it. The trusted gateway address is therefore reachable only from the server: nobody outside can connect to port 8000 and pass themselves off as the trusted proxy, whatever `X-Forwarded-For` they send.

Containers reach Vexa on the host through `host.docker.internal:8056` (`extra_hosts: host-gateway`).

Minimum server: **4 GB memory, 2 vCPUs**, Ubuntu 24.04. Vexa Lite alone used about 830 MB idle in the spike, and a bot in a call needs more.

Commands below use `notes.example.com` and `/opt/beyric-note-taker`. Replace them.

## 0. What the owner must supply

- [ ] An Ubuntu 24.04 server (4 GB, 2 vCPUs) with a public IPv4 address and SSH access.
- [ ] A domain name: a DNS `A` record for the chosen host name (for example `notes.yourdomain.com`) pointing at the server. Add an `AAAA` record only if the server has working IPv6.
- [ ] An email address for Let's Encrypt expiry notices.
- [ ] An OpenAI API key (used by Vexa for `whisper-1` and by the app for notes), with a spend limit set in the OpenAI dashboard.
- [ ] The owner's login password for the app.
- [ ] Access to the provider's firewall settings (step 1).
- [ ] A decision on who gets a copy of the backups, and where (this guide keeps them on the server only; see Follow-ups).

## 1. Provider firewall: allow only 22, 80 and 443

Do this in the **provider's** firewall (cloud firewall or security group), before anything is installed.

- Inbound allow: TCP 22 (SSH), TCP 80, TCP 443. Everything else denied.
- Do not rely on `ufw` alone. Docker writes its own iptables rules for published ports, and they take effect before `ufw`'s rules, so `ufw deny 8056` does not stop a published container port.
- This matters here because Vexa Lite publishes its ports 8056 (API), 3001 (terminal workbench) and 8100 (agent API) on **all interfaces**. Anyone who can reach them can drive Vexa. The provider firewall is what keeps them closed.

Verify from a machine **outside** the server (your laptop, not the server itself):

```bash
for p in 22 80 443 8056 3001 8100 8000; do
  printf '%s: ' "$p"; nc -z -w 3 SERVER_IP "$p" && echo open || echo closed/filtered
done
```

Expected once the server is fully set up: 22, 80, 443 open; 8056, 3001, 8100 and 8000 closed/filtered. Run this once now (all but 22 closed) and again in step 9.

## 2. Server preparation

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install ca-certificates curl git make nginx certbot
# Docker Engine and the compose plugin: follow https://docs.docker.com/engine/install/ubuntu/
sudo usermod -aG docker "$USER"      # log out and back in afterwards
docker compose version
```

Stop nginx's default page from interfering until our config is in place:

```bash
sudo rm -f /etc/nginx/sites-enabled/default
```

### Confirm no Claude credentials are present

Vexa's start script mounts `~/.claude/.credentials.json` into its container if that file exists. A server should never have it.

```bash
ls -l ~/.claude/.credentials.json    # expected: "No such file or directory"
sudo ls -l /root/.claude/.credentials.json   # same, if you will run make as root
```

If the file exists, stop and find out why before going on. Do not start Vexa until it is gone.

## 3. Vexa Lite, pinned to commit `31122f9`

```bash
git clone https://github.com/Vexa-ai/vexa.git ~/vexa
cd ~/vexa
git checkout 31122f9
git rev-parse --short HEAD           # must print 31122f9
```

(If the repository URL has moved, use the same URL the spike used; the commit is what matters.)

Mint the secrets once. Each is a long random value; do not reuse any, and do not use the placeholders Vexa warns about.

```bash
cd ~/vexa
umask 077
cat > .env <<EOF
ADMIN_TOKEN=$(openssl rand -hex 32)
INTERNAL_API_SECRET=$(openssl rand -hex 32)
NEXTAUTH_SECRET=$(openssl rand -hex 32)
JWT_SECRET=$(openssl rand -hex 32)
TRANSCRIPTION_SERVICE_URL=https://api.openai.com
TRANSCRIPTION_SERVICE_TOKEN=PASTE_OPENAI_KEY_HERE
TRANSCRIPTION_MODEL=whisper-1
IMAGE_TAG=v012
EOF
chmod 600 .env
nano .env                            # paste the OpenAI key
```

Notes on these values:

- `TRANSCRIPTION_SERVICE_URL`, `TRANSCRIPTION_SERVICE_TOKEN` and `TRANSCRIPTION_MODEL=whisper-1` turn on **live transcription**. Keep it on: with it off, Vexa saves the recording but returns no segments or speaker names (found in the spike). It costs about $0.36 per meeting hour.
- `ADMIN_TOKEN`, `INTERNAL_API_SECRET`, `NEXTAUTH_SECRET` and `JWT_SECRET` are minted here so Vexa never falls back to a value it prints in its own source (`deploy/lite/entrypoint.sh`: the admin and internal secrets are otherwise minted per boot, the other two default to published literals). Vexa's Makefile passes the whole file to the container with `--env-file`. `ADMIN_TOKEN` is the key to Vexa's admin API; treat it like a root password.
- Vexa's Postgres and storage sidecars use fixed internal credentials, but they sit on the private `vexa-lite-net` Docker network and publish no port, so they are not reachable from outside. Leave them alone.
- Variable names come from the Vexa checkout at `31122f9`. If a later commit renames any, re-check with `grep -n NAME deploy/lite/entrypoint.sh deploy/lite/Makefile`.
- Keep `~/vexa/.env` out of git and out of backups that leave the server.

Start it:

```bash
cd ~/vexa
make -C deploy/lite up
docker ps --format '{{.Names}}\t{{.Status}}'      # vexa-lite, vexa-lite-postgres, vexa-lite-storage
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8056/health      # expect 200
```

The first start pulls images and takes a few minutes. Vexa restarts with Docker; confirm after a reboot in step 9.

### Create the Vexa API key for the app

The admin API listens only inside the Vexa container (port 8001), so run the calls there. The admin token is read from the container's own environment, so it never appears in your shell history.

```bash
docker exec vexa-lite python3 - <<'PY'
import json, os, urllib.request
base = "http://127.0.0.1:8001"
hdr = {"X-Admin-API-Key": os.environ["ADMIN_API_TOKEN"], "Content-Type": "application/json"}
def call(path, body):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), hdr, method="POST")
    return json.load(urllib.request.urlopen(req))
user = call("/admin/users", {"email": "notes-app@localhost", "name": "Beyric Note Taker", "max_concurrent_bots": 1})
tok = call(f"/admin/users/{user['id']}/tokens", {"scopes": ["bot", "tx"]})
print(tok["token"])
PY
```

It prints a `vxa_bot_...` key **once**. Copy it into the app's `.env` (step 4). Vexa's authentication docs describe these two calls (`POST /admin/users`, `POST /admin/users/{id}/tokens`); if the pinned commit rejects the body, check `docs/docs/authentication.mdx` in the checkout.

## 4. App `.env`

```bash
sudo mkdir -p /opt/beyric-note-taker && sudo chown "$USER": /opt/beyric-note-taker
git clone REPO_URL /opt/beyric-note-taker        # the owner supplies the repository URL
cd /opt/beyric-note-taker
git checkout main                                # or the release tag
cp .env.example .env && chmod 600 .env
```

Edit `.env`:

| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD` | `openssl rand -hex 24` |
| `DATABASE_URL` | unused by compose (it builds its own from `POSTGRES_PASSWORD`); leave or match |
| `VEXA_API_KEY` | the `vxa_bot_...` key from step 3 |
| `VEXA_BASE_URL` | leave; compose overrides it to `http://host.docker.internal:8056` |
| `OPENAI_API_KEY` | the OpenAI key |
| `OWNER_PASSWORD` | a long password (the app refuses an empty one) |
| `SESSION_SECRET` | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `APP_ENV` | `production` |
| `COOKIE_SECURE` | `true` |
| `TRUSTED_PROXY_IPS` | `172.28.0.1` (nginx's address as the container sees it). Leave the line out and compose uses the same address; set it **empty** (`TRUSTED_PROXY_IPS=`) and no proxy is trusted at all |

Leave `TRANSCRIPTION_MODEL` and `NOTES_MODEL` at the `.env.example` values unless told otherwise.

## 5. Start the app and migrate

```bash
cd /opt/beyric-note-taker
docker compose config > /dev/null && echo config ok
docker compose build
docker compose up -d db
docker compose run --rm migrate          # alembic upgrade head
docker compose up -d web worker
docker compose ps
curl -s http://127.0.0.1:8000/health
```

Expected: `db` and `web` healthy, `worker` running, `/health` returns OK. `migrate` also runs automatically before `web` and `worker` on `docker compose up`.

Check the worker reaches Vexa through the host:

```bash
docker compose exec worker python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:8056/health', timeout=5).status)"   # 200
```

### Audio volume ownership

`web` and `worker` run as the non-root user `app`. The image creates `/srv/data/audio` owned by `app`, so a **new** `audiodata` volume is writable. A volume created by an older version of this stack is owned by root and must be fixed once:

```bash
docker compose run --rm --user root worker chown -R app:app /srv/data/audio
```

## 6. HTTPS with nginx and Let's Encrypt

DNS for the host name must already resolve to this server and port 80 must be open (step 1).

```bash
cd /opt/beyric-note-taker
DOMAIN=notes.yourdomain.com          # your real host name
EMAIL=you@yourdomain.com
sudo mkdir -p /var/www/certbot

# 6a. Temporary HTTP-only site, only to answer the certificate challenge
sudo tee /etc/nginx/conf.d/beyric-notes.conf >/dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 404; }
}
EOF
sudo nginx -t && sudo systemctl reload nginx

# 6b. Get the certificate (renewal reuses the same webroot method)
sudo certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" -m "$EMAIL" --agree-tos --no-eff-email

# 6c. Install the real site
sudo sed "s/notes\.example\.com/$DOMAIN/g" deploy/nginx.conf | sudo tee /etc/nginx/conf.d/beyric-notes.conf >/dev/null
sudo nginx -t && sudo systemctl reload nginx
```

Renewal: Ubuntu's `certbot` package installs a systemd timer. Make it reload nginx and test it:

```bash
echo -e '#!/bin/sh\nsystemctl reload nginx' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh >/dev/null
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
sudo certbot renew --dry-run
systemctl list-timers | grep certbot
```

What `deploy/nginx.conf` does: redirects HTTP to HTTPS; proxies to `127.0.0.1:8000` (waiting up to 60 s for a reply, 300 s for rewriting notes, `/meetings/<id>/regenerate`); **overwrites** `X-Forwarded-For` with the connecting address (a client-sent value is discarded, so it cannot dodge the app's login limit); sets `Host` and `X-Forwarded-Proto`; limits POST `/login` to 6 a minute per address (burst 3, then HTTP 429) and all traffic to 20 requests a second; adds HSTS (1 year, no subdomains, no preload), `X-Frame-Options: DENY` and a `Permissions-Policy`. It leaves the CSP, `nosniff`, `Referrer-Policy` and `Cache-Control` to the app, which already sets them.

## 7. Daily backup

```bash
sudo mkdir -p /var/backups/beyric-notes
sudo chown "$USER": /var/backups/beyric-notes
chmod 700 /var/backups/beyric-notes
/opt/beyric-note-taker/deploy/backup.sh          # run once by hand
ls -l /var/backups/beyric-notes                  # one notetaker-*.sql.gz, mode -rw-------
```

Schedule it (as the same user, who is in the `docker` group):

```bash
crontab -e
```

```
17 3 * * * /opt/beyric-note-taker/deploy/backup.sh >> /var/backups/beyric-notes/backup.log 2>&1
```

The script runs `pg_dump` inside the `db` container, gzips it, tests the archive, writes with mode 600 and deletes dumps older than 14 days (`KEEP_DAYS`). It stops on any error (`set -euo pipefail`) and never leaves a partial file behind. It backs up the database only. Recordings are temporary (7 days by default) and are not backed up.

### Restore (practice this once before you need it)

Always restore to a scratch database first and look at the data. This never touches the live `notetaker` database.

```bash
cd /opt/beyric-note-taker
FILE=/var/backups/beyric-notes/notetaker-YYYYMMDDTHHMMSSZ.sql.gz     # pick a dump

docker compose exec -T db psql -U postgres -c 'CREATE DATABASE notetaker_restore'
gzip -dc "$FILE" | docker compose exec -T db psql -U postgres -d notetaker_restore -v ON_ERROR_STOP=1 -q
docker compose exec -T db psql -U postgres -d notetaker_restore -c 'SELECT count(*) AS meetings, count(notes) AS with_notes FROM meetings'
docker compose exec -T db psql -U postgres -c 'DROP DATABASE notetaker_restore'
```

Notes live in the `meetings.notes` column, so one query covers both. Compare the counts with the live database (`... -d notetaker -c ...`). Expected: meetings and notes are present.

**Restoring over the live database** (disaster recovery only; this replaces current data):

```bash
docker compose stop web worker
docker compose exec -T db psql -U postgres -c 'DROP DATABASE notetaker' -c 'CREATE DATABASE notetaker'
gzip -dc "$FILE" | docker compose exec -T db psql -U postgres -d notetaker -v ON_ERROR_STOP=1 -q
docker compose up -d web worker
```

## 8. Updating and rolling back

Update:

```bash
cd /opt/beyric-note-taker
/opt/beyric-note-taker/deploy/backup.sh           # a fresh backup first
git fetch --tags && git pull --ff-only
git rev-parse --short HEAD > /tmp/beyric-deployed   # note the new head
docker compose build
docker compose up -d                              # migrate runs first, then web and worker restart
docker compose ps && curl -s http://127.0.0.1:8000/health
```

Roll back the app:

```bash
git log --oneline -5                              # find the previous good commit
git checkout PREVIOUS_COMMIT
docker compose build && docker compose up -d
```

If the update included a database migration, code from before it may not run on the new schema. Either `docker compose run --rm migrate alembic downgrade -1` (when the migration has a working downgrade) before checking out the old code, or restore the pre-update backup (step 7). Take the backup first; it is the only safe route.

Update nginx config after editing `deploy/nginx.conf`: repeat 6c, which runs `nginx -t` before reload.

Update Vexa: do not move off commit `31122f9` without testing a bot on a spare meeting first. To stop it: `make -C deploy/lite down` (volumes are kept).

## 9. Verification checklist

From **outside** the server:

```bash
for p in 22 80 443 8056 3001 8100 8000 5432; do
  printf '%s: ' "$p"; nc -z -w 3 SERVER_IP "$p" && echo open || echo closed/filtered
done
curl -sI http://notes.yourdomain.com/ | head -3                      # 301 to https
curl -sI https://notes.yourdomain.com/login | grep -i -E 'HTTP|strict|x-frame|content-security|cache-control'
curl -s -o /dev/null -w '%{http_code}\n' https://notes.yourdomain.com/health   # 200
```

- [ ] 22, 80, 443 open; 8056, 3001, 8100, 8000, 5432 closed or filtered.
- [ ] HTTP redirects to HTTPS; HTTPS shows the login page; the browser shows a valid certificate.
- [ ] Response headers show one `Content-Security-Policy` (the app's), plus HSTS and `X-Frame-Options`.
- [ ] Login with the owner password works; the cookie is `Secure`.
- [ ] Spoof test. A login POST needs the session cookie and its CSRF token, or the app refuses it with 403 before it counts anything. Get both, then send wrong passwords with a different made-up `X-Forwarded-For` each time:

  ```bash
  HOST=https://notes.yourdomain.com
  JAR=$(mktemp)
  TOKEN=$(curl -s -c "$JAR" "$HOST/login" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p')
  for i in 1 2 3 4 5 6 7 8; do
    curl -s -b "$JAR" -o /dev/null -w '%{http_code}\n' -X POST "$HOST/login" \
      -d 'password=wrong' -d "csrf_token=$TOKEN" -H "X-Forwarded-For: 10.9.8.$i"
  done
  rm -f "$JAR"
  ```

  Expected: the first few answers are `401`, then every one is `429`. The `429` comes from nginx (6 a minute, burst 3), keyed on the real connecting address. Changing the made-up `X-Forwarded-For` must not bring `401` back. (The app's own limit, 5 failures in 15 minutes, shows "Too many failed attempts" instead of nginx's plain error page.)
- [ ] `docker compose ps`: `db`, `web` healthy; `worker` up. `docker compose logs worker` shows no Vexa connection errors.
- [ ] After `sudo reboot`: Vexa containers, the app and nginx all come back (`docker ps`, `systemctl status nginx`).
- [ ] `backup.sh` produced a dump; the restore to a scratch database shows meetings and notes.
- [ ] Task 8 acceptance: send the bot to a real meeting end to end.

## Follow-ups

- Backups stay on the same server, so they do not survive losing it. Copy them elsewhere (object storage or another host) once the owner picks a place.
- The `docker exec` admin calls are the only way to mint keys; there is no rotation procedure yet. To rotate, mint a new key, update `.env`, `docker compose up -d web worker`, then delete the old key in Vexa.
- Nothing monitors the stack. Add an uptime check on `/health` (it returns `503` when the database is down).
- Server hardening beyond the provider firewall (SSH keys only, unattended upgrades, fail2ban) is the owner's call.
- Steps 1 to 9 have not been run on a server yet. Only the files were validated locally (see `.superpowers/sdd/2026-11-02-beyric-note-taker-mvp/task-7-report.md`).
