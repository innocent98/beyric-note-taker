#!/bin/sh
# Widen Vexa's transcriptions.language from varchar(10) to varchar(32).
#
# whisper-1 (Vexa's live hints) labels segments with a language name, and "haitian creole" is 14
# characters, so Vexa's own varchar(10) rejects whole batches and we get no speaker names. Vexa
# creates the table itself on first start, so this waits for it. Safe to run on every deploy: it
# does nothing when the column is already wide enough. See docs/sop/2026-11-02-vexa-language-column.md.
#
# Reads the standard libpq variables (PGHOST, PGUSER, PGPASSWORD, PGDATABASE).
set -eu

WANTED=32
tries=0
while :; do
    current=$(psql -tA -c "select character_maximum_length from information_schema.columns where table_name = 'transcriptions' and column_name = 'language'" 2>/dev/null || true)
    [ -n "$current" ] && break
    tries=$((tries + 1))
    if [ "$tries" -ge 60 ]; then
        echo "vexa-schema-fix: transcriptions.language not found after 5 minutes; is Vexa up?" >&2
        exit 1
    fi
    sleep 5
done

if [ "$current" -ge "$WANTED" ]; then
    echo "vexa-schema-fix: language is already varchar($current); nothing to do"
    exit 0
fi

# lock_timeout so the ALTER can never queue behind Vexa's writes and block them in turn.
psql -v ON_ERROR_STOP=1 <<SQL
set lock_timeout = '3s';
alter table transcriptions alter column language type varchar($WANTED);
SQL
echo "vexa-schema-fix: language widened from varchar($current) to varchar($WANTED)"
