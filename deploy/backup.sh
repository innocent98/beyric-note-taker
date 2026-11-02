#!/usr/bin/env bash
# Daily Postgres backup for Beyric Note Taker.
# Writes gzip dumps to BACKUP_DIR (mode 700 dir, 600 files) and deletes dumps older than
# KEEP_DAYS. Run from cron as a user that can run `docker compose` (see deploy/README.md).
set -euo pipefail
umask 077

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/beyric-notes}"
KEEP_DAYS="${KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="$BACKUP_DIR/notetaker-$stamp.sql.gz"
partial="$final.partial"
trap 'rm -f "$partial"' EXIT

cd "$PROJECT_DIR"
# pipefail makes a pg_dump failure fail the whole pipeline; the partial file is then removed.
docker compose exec -T db pg_dump -U postgres --no-owner notetaker | gzip -9 > "$partial"

gzip -t "$partial"
if [ "$(gzip -dc "$partial" | wc -c)" -lt 1000 ]; then
    echo "backup looks empty, refusing to keep it" >&2
    exit 1
fi
chmod 600 "$partial"
mv "$partial" "$final"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'notetaker-*.sql.gz' -mtime +"$KEEP_DAYS" -delete

echo "backup ok: $final ($(wc -c < "$final") bytes)"
