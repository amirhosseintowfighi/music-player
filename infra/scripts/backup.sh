#!/usr/bin/env bash
# Daily Postgres backup. Writes a compressed custom-format dump, prunes old ones and
# publishes a Prometheus textfile metric so a missed backup pages someone.
#
#   0 2 * * *  /opt/tmusic/infra/scripts/backup.sh >> /var/log/tmusic-backup.log 2>&1
set -Eeuo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/tmusic}"
KEEP_DAYS="${KEEP_DAYS:-14}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
DB_CONTAINER="${DB_CONTAINER:-postgres}"
DB_NAME="${POSTGRES_DB:-tmusic}"
DB_USER="${POSTGRES_USER:-tmusic}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="${BACKUP_DIR}/tmusic-${stamp}.dump"
mkdir -p "$BACKUP_DIR"

report_failure() {
  if [ -d "$TEXTFILE_DIR" ]; then
    printf 'tmusic_backup_last_failure_timestamp %s\n' "$(date +%s)" \
      > "${TEXTFILE_DIR}/tmusic_backup.prom.$$"
    mv "${TEXTFILE_DIR}/tmusic_backup.prom.$$" "${TEXTFILE_DIR}/tmusic_backup_failure.prom"
  fi
  echo "backup FAILED" >&2
}
trap report_failure ERR

# -Fc keeps it restorable table by table, which is what a partial recovery needs.
docker compose exec -T "$DB_CONTAINER" \
  pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc --no-owner --no-privileges > "$target"

size=$(stat -c %s "$target")
if [ "$size" -lt 100000 ]; then
  echo "dump is suspiciously small (${size} bytes)" >&2
  exit 1
fi

# Fail loudly if the dump cannot even be listed: a corrupt file is not a backup.
pg_restore --list "$target" > /dev/null

find "$BACKUP_DIR" -name 'tmusic-*.dump' -mtime "+${KEEP_DAYS}" -delete

if [ -d "$TEXTFILE_DIR" ]; then
  {
    printf 'tmusic_backup_last_success_timestamp %s\n' "$(date +%s)"
    printf 'tmusic_backup_size_bytes %s\n' "$size"
  } > "${TEXTFILE_DIR}/tmusic_backup.prom.$$"
  mv "${TEXTFILE_DIR}/tmusic_backup.prom.$$" "${TEXTFILE_DIR}/tmusic_backup.prom"
fi

echo "backup ok: ${target} (${size} bytes)"
