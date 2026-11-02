# Deploying Beyric Note Taker

One Ubuntu 24.04 server runs everything. It may be shared with other apps (the first server is),
so nothing here takes a public port except through nginx.

| Piece | Where it runs | Reachable from |
|---|---|---|
| App: `db`, `migrate`, `worker`, `web` | Docker Compose in this repo | `web` on `127.0.0.1:8020` only |
| Vexa Lite: `vexa`, `vexa-db`, `vexa-storage` | the same Compose project | the app, as `vexa:8056`; the server only, on `127.0.0.1:18056` |
| nginx + certbot | **on the host**, not in Docker | the internet, ports 80 and 443 |

- Vexa is a ready-made image, `vexaai/vexa-lite:v012`; there is nothing to install or build. It
  runs from `compose.yaml` rather than Vexa's own Makefile because the Makefile publishes ports
  8056, 3001 and 8100 on every interface and mounts `~/.claude/.credentials.json` if it exists.
  Here Vexa's terminal and agent ports are not published at all, and no host file is mounted.
- `vexa` is capped at 3 GB of memory, so a bot running Chrome cannot starve other apps.
- Three networks: `default` (web, worker, vexa; has internet), `appdb` (the app database; no
  internet) and `vexainternal` (Vexa's database and storage; no internet). A bot runs Chrome on
  untrusted meeting pages, so the Vexa container cannot reach the app database at all.
- nginx connects to `127.0.0.1:8020`, so the app sees nginx as the Docker bridge gateway
  `172.28.0.1` (`compose.yaml` pins that network). That is the one trusted proxy. Only processes on
  the server can reach port 8020, so nobody outside can pose as that proxy.
- Minimum server: **4 GB memory, 2 vCPUs** free for this app. Vexa used about 540 MB idle in a
  local run and about 1.4 GB with a bot in a call.

Paths below: the app lives in `/opt/beyric-note-taker` (like `/opt/haven`), backups in `~/backups/beyric-notes`, and the
domain is `notes.beyrictech.com`. Steps marked **(sudo)** need the server owner's password.

## 0. What the owner must supply

- [ ] SSH access as a user in the `docker` group, and someone who can run `sudo`.
- [ ] A DNS `A` record for the domain pointing at the server. Proxied through Cloudflare (orange
      cloud) is supported: `deploy/nginx.conf` restores each visitor's real address from
      `CF-Connecting-IP`. Set Cloudflare's SSL/TLS mode to **Full (strict)**; "Flexible" would
      loop forever against the HTTP-to-HTTPS redirect certbot adds.
- [ ] An email address for Let's Encrypt expiry notices.
- [ ] An OpenAI API key with a spend limit set in the OpenAI dashboard.
- [ ] The owner's login password for the app.

## 1. Ports: check from outside

Only 22, 80 and 443 should answer. Docker bypasses `ufw` for published ports, so the real guard
is that `compose.yaml` publishes nothing on a public interface. Check from **your laptop**:

```bash
for p in 22 80 443 8020 18056 8056 3001 8100 5432; do
  printf '%s: ' "$p"; nc -z -G 3 SERVER_IP "$p" && echo open || echo closed
done
```

Expected: 22, 80, 443 open; the rest closed. (Another app's port may show open; that is that
app's to fix, not this one's.) Run it again in step 9.

## 2. Server preparation

Docker, the compose plugin, nginx and certbot must be installed. Then:

```bash
ls -l ~/.claude/.credentials.json    # expected: "No such file or directory"
```

### Read access to the repository

The server pulls the code with its own read-only deploy key.

```bash
ssh-keygen -t ed25519 -N '' -C beyric-notes-server -f ~/.ssh/beyric_notes_github
cat >> ~/.ssh/config <<'EOF'
Host github-beyric-notes
    HostName github.com
    User git
    IdentityFile ~/.ssh/beyric_notes_github
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
cat ~/.ssh/beyric_notes_github.pub
```

Add that public key to the GitHub repository as a **read-only deploy key**
(`gh repo deploy-key add <file> --repo innocent98/beyric-note-taker --title server`), then:

```bash
sudo mkdir -p /opt/beyric-note-taker && sudo chown "$USER": /opt/beyric-note-taker    # (sudo)
git clone git@github-beyric-notes:innocent98/beyric-note-taker.git /opt/beyric-note-taker
```

## 3. Secrets: `.env.vexa` and `.env`

Both files live only on the server, are git-ignored, and are never overwritten by a deploy.

```bash
cd /opt/beyric-note-taker
umask 077
T=$(openssl rand -hex 32)
cat > .env.vexa <<EOF
ADMIN_TOKEN=$T
ADMIN_API_TOKEN=$T
INTERNAL_API_SECRET=$(openssl rand -hex 32)
NEXTAUTH_SECRET=$(openssl rand -hex 32)
JWT_SECRET=$(openssl rand -hex 32)
TRANSCRIPTION_SERVICE_URL=https://api.openai.com
TRANSCRIPTION_SERVICE_TOKEN=PASTE_OPENAI_KEY
TRANSCRIPTION_MODEL=whisper-1
EOF
cp .env.example .env
```

- Live transcription (`TRANSCRIPTION_*`) must stay on: without it Vexa returns no speaker names.
  It costs about $0.36 per meeting hour.
- The secrets are minted so Vexa never falls back to values printed in its own source.
  `ADMIN_TOKEN` is the key to Vexa's admin API; treat it like a root password.
- Vexa's own database and storage get random passwords from `.env` (below), unlike Vexa's
  Makefile, which uses fixed ones.

Edit `.env`:

| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD`, `VEXA_DB_PASSWORD`, `VEXA_STORAGE_SECRET` | each `openssl rand -hex 24` |
| `VEXA_API_KEY` | from step 4 |
| `DATABASE_URL`, `VEXA_BASE_URL` | leave commented; compose sets both |
| `OPENAI_API_KEY` | the OpenAI key |
| `OWNER_PASSWORD` | a long password |
| `SESSION_SECRET` | `openssl rand -hex 48` |
| `APP_ENV`, `COOKIE_SECURE` | `production`, `true` |
| `TRUSTED_PROXY_IPS` | `172.28.0.1`. Set it **empty** and no proxy is trusted at all |

## 4. Start Vexa and create the app's key

```bash
cd /opt/beyric-note-taker
docker compose up -d vexa              # also starts vexa-db and vexa-storage
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18056/health    # 200
deploy/vexa-api-key.sh                 # prints vxa_bot_... once; put it in .env as VEXA_API_KEY
```

The first start pulls images and takes a few minutes. Run `vexa-api-key.sh` once: a second run
fails because the Vexa user already exists.

## 5. Start the app

```bash
docker compose up -d --build           # migrate runs first, then web and worker
docker compose ps
curl -s http://127.0.0.1:8020/health   # ok
docker compose exec worker python -c "import urllib.request; print(urllib.request.urlopen('http://vexa:8056/health', timeout=5).status)"   # 200
```

`web` and `worker` run as the non-root user `app`; the image makes `/srv/data/audio` theirs. A
volume left by an older version is owned by root and needs, once:
`docker compose run --rm --user root worker chown -R app:app /srv/data/audio`.

## 6. HTTPS with nginx and Let's Encrypt (sudo)

Same layout as the server's other sites: a file in `sites-available`, linked into `sites-enabled`,
then `certbot --nginx` adds HTTPS to it.

```bash
cd /opt/beyric-note-taker
sudo cp deploy/nginx.conf /etc/nginx/sites-available/beyric-notes
sudo ln -s /etc/nginx/sites-available/beyric-notes /etc/nginx/sites-enabled/beyric-notes
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d notes.beyrictech.com
```

`nginx -t` checks every site on the server, so a mistake stops before anything reloads. The file
shares `http {}` with other sites, so its zone names are prefixed (`notes_login`,
`notes_general`), and `limit_req_status` and the Cloudflare real-IP settings sit inside its own
server block, so they change nothing for Haven or Novex.

If certbot's challenge fails while the record is proxied (usually Cloudflare's "Always Use HTTPS"
redirecting the challenge), switch the record to DNS only, run certbot, and switch it back.

After a later change to `deploy/nginx.conf`, do not copy it over the live file (that would drop
certbot's lines): edit `/etc/nginx/sites-available/beyric-notes` by hand, then `sudo nginx -t &&
sudo systemctl reload nginx`.

What the site does: proxies to `127.0.0.1:8020` (60 s, or 300 s for
`/meetings/<id>/regenerate`); takes the visitor's address from `CF-Connecting-IP`, trusted only
from Cloudflare's published ranges; **overwrites** `X-Forwarded-For` with that address, so a client
cannot dodge the login limit; limits POST `/login` to 6 a minute per address (burst 3, then 429)
and all traffic to 20 requests a second; adds HSTS (1 year), `X-Frame-Options: DENY` and a
`Permissions-Policy`. The app sets its own CSP, `nosniff`, `Referrer-Policy` and `Cache-Control`.

Cloudflare closes a request after 100 s (free plan). Only rewriting notes can take that long; the
page then shows Cloudflare's 524 error, and the rewrite is likely, but not verified, to finish on
the server. Reload the meeting page.

## 7. Daily backup

```bash
BACKUP_DIR=~/backups/beyric-notes /opt/beyric-note-taker/deploy/backup.sh     # once by hand
crontab -e
```

```
17 3 * * * BACKUP_DIR=$HOME/backups/beyric-notes /opt/beyric-note-taker/deploy/backup.sh >> $HOME/backups/beyric-notes/backup.log 2>&1
```

The script dumps the app database inside the `db` container, gzips and tests it, writes mode 600,
and deletes dumps older than 14 days. Recordings are temporary and not backed up; Vexa's own
database is not backed up either (it only holds meeting records the app has already copied).

### Restore (practise once)

```bash
cd /opt/beyric-note-taker
FILE=~/backups/beyric-notes/notetaker-YYYYMMDDTHHMMSSZ.sql.gz
docker compose exec -T db psql -U postgres -c 'CREATE DATABASE notetaker_restore'
gzip -dc "$FILE" | docker compose exec -T db psql -U postgres -d notetaker_restore -v ON_ERROR_STOP=1 -q
docker compose exec -T db psql -U postgres -d notetaker_restore -c 'SELECT count(*) AS meetings, count(notes) AS with_notes FROM meetings'
docker compose exec -T db psql -U postgres -c 'DROP DATABASE notetaker_restore'
```

Over the live database (disaster recovery only):

```bash
docker compose stop web worker
docker compose exec -T db psql -U postgres -c 'DROP DATABASE notetaker' -c 'CREATE DATABASE notetaker'
gzip -dc "$FILE" | docker compose exec -T db psql -U postgres -d notetaker -v ON_ERROR_STOP=1 -q
docker compose up -d web worker
```

## 8. Continuous deployment, updating and rolling back

Merging a PR into `main` deploys it: `ci` runs, and when it passes `.github/workflows/deploy.yml`
connects over SSH and runs `deploy/deploy.sh <sha>`. That script refuses any commit not on
`origin/main`, backs up the database, checks out the commit, builds, starts, and waits up to two
minutes for `/health`. If the new version never turns healthy it starts the previous commit again
and the workflow fails.

### Deploy key setup (once)

GitHub needs a private key to log in. It gets its own key, not the owner's: a key stored in
GitHub can be read by any workflow change, and the owner's key opens a full shell on a server that
also runs Haven and Novex. This key can do one thing, run `deploy/deploy.sh` with a commit SHA,
and removing its one line from `authorized_keys` revokes it without touching anyone's access.
On the server:

```bash
ssh-keygen -t ed25519 -N '' -C beyric-notes-cd -f ~/beyric-notes-cd
echo "command=\"/opt/beyric-note-taker/deploy/deploy.sh\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $(cat ~/beyric-notes-cd.pub)" >> ~/.ssh/authorized_keys
```

Store these repository secrets, then delete `~/beyric-notes-cd*` from the server:

| Secret | Value |
|---|---|
| `DEPLOY_SSH_KEY` | contents of `~/beyric-notes-cd` (the private key) |
| `DEPLOY_HOST` | the server IP |
| `DEPLOY_USER` | the SSH user |
| `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan -t ed25519 SERVER_IP` output |

### By hand

```bash
cd /opt/beyric-note-taker && git fetch origin main
deploy/deploy.sh "$(git rev-parse origin/main)"
```

Roll back: re-deploy an older commit of `main` with `deploy/deploy.sh <sha>`, or revert the PR on
GitHub so CD deploys the revert. If the bad version ran a migration, older code may not run on the
new schema: `docker compose run --rm migrate alembic downgrade -1` first, or restore the backup the
deploy took (step 7).

Update Vexa: change `VEXA_IMAGE_TAG` (in `.env`, default `v012`) only after a test call on the new
tag; `docker compose up -d vexa` applies it, and setting the old tag back rolls it back.
After the upgrade, check the language column is still wide: `docker compose up -d vexa-schema-fix`
runs the idempotent fix again, and `docker compose logs vexa-schema-fix` should say "language is
already varchar(32)" or "widened". Vexa's own migrations may touch that table, so keep this step.

## 9. Verification checklist

From **outside** the server:

```bash
curl -sI http://notes.beyrictech.com/ | head -3                                   # 301 to https
curl -sI https://notes.beyrictech.com/login | grep -i -E 'HTTP|strict|x-frame|content-security'
curl -s -o /dev/null -w '%{http_code}\n' https://notes.beyrictech.com/health      # 200
```

- [ ] Ports as in step 1.
- [ ] HTTPS shows the login page with a valid certificate; one `Content-Security-Policy`, plus HSTS
      and `X-Frame-Options`.
- [ ] Login works and the cookie is `Secure`.
- [ ] Spoof test: wrong passwords with a different made-up `X-Forwarded-For` each time go `401`,
      then `429`, and changing the header does not bring `401` back:

  ```bash
  HOST=https://notes.beyrictech.com; JAR=$(mktemp)
  TOKEN=$(curl -s -c "$JAR" "$HOST/login" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p')
  for i in 1 2 3 4 5 6 7 8; do
    curl -s -b "$JAR" -o /dev/null -w '%{http_code}\n' -X POST "$HOST/login" \
      -d 'password=wrong' -d "csrf_token=$TOKEN" -H "X-Forwarded-For: 10.9.8.$i"
  done; rm -f "$JAR"
  ```

  Run it in bash. To test the Cloudflare header, send `-H "CF-Connecting-IP: 10.7.7.$i"` straight
  to the server (`--resolve notes.beyrictech.com:443:SERVER_IP`); through Cloudflare that header
  gets a 403 from Cloudflare itself. Expect your own address in `docker compose logs web`. The test
  locks your own address out of login for 15 minutes (the app's limit).
- [ ] `docker compose ps`: `db`, `web`, `vexa` healthy; `worker` up; no Vexa errors in
      `docker compose logs worker`.
- [ ] Other apps on the server still answer after the install.
- [ ] After a reboot, the stack and nginx come back.
- [ ] A backup exists and the scratch restore shows meetings and notes.
- [ ] A merge to `main` deploys through CD.
- [ ] Task 8 acceptance: a real meeting end to end.

## Follow-ups

- Backups stay on the same server. Copy them elsewhere once the owner picks a place.
- No rotation procedure for the Vexa key: mint a new one with the admin API, update `.env`,
  `docker compose up -d web worker`, then delete the old key.
- Nothing monitors the stack. Add an uptime check on `/health` (it returns 503 when the database
  is down).
