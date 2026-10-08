#!/usr/bin/env bash
# Database backup for the home-server stack; run it nightly from cron.
#
# Writes a pg_dump custom-format archive to the db container's /backups mount
# (HOMEBASE_BACKUP_DIR, see docker-compose.override.example.yml), checks that the
# archive is readable, and deletes archives older than RETENTION_DAYS (default 14).
# Every run appends to backup.log in the stack directory. A failure also leaves a
# BACKUP_FAILED file there, holding the time of the failure, until the next good run.
set -euo pipefail

STACK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
LOG="$STACK_DIR/backup.log"
cd "$STACK_DIR"

log() { printf '%s %s\n' "$(date -Is)" "$*" >> "$LOG"; }

on_error() {
    log "FAILED (exit $?) at line $1; see output above"
    date -Is > "$STACK_DIR/BACKUP_FAILED"
}
trap 'on_error $LINENO' ERR

exec >> "$LOG" 2>&1
log "backup started"

name="homebase-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T -e NAME="$name" -e RETENTION_DAYS="$RETENTION_DAYS" db sh -euc '
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file "/backups/$NAME.partial"
    pg_restore --list "/backups/$NAME.partial" > /dev/null
    mv "/backups/$NAME.partial" "/backups/$NAME"
    find /backups -maxdepth 1 -name "homebase-*.dump" -mtime "+$((RETENTION_DAYS - 1))" -print -delete
    find /backups -maxdepth 1 -name "homebase-*.dump.partial" -mmin +60 -print -delete
    ls -l "/backups/$NAME"
'

rm -f "$STACK_DIR/BACKUP_FAILED"
log "backup finished: $name"
