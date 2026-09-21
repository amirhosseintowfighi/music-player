#!/usr/bin/env bash
# Weekly restore drill: a backup nobody has restored is a hope, not a backup.
# Restores the newest dump into a throwaway database and checks that the schema and
# the row counts survived, then drops it.
#
#   0 4 * * 0  /opt/tmusic/infra/scripts/restore-test.sh
set -Eeuo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/tmusic}"
DB_CONTAINER="${DB_CONTAINER:-postgres}"
DB_USER="${POSTGRES_USER:-tmusic}"
TEST_DB="tmusic_restore_test_$(date -u +%Y%m%d)"

newest="$(ls -1t "${BACKUP_DIR}"/tmusic-*.dump 2>/dev/null | head -n1 || true)"
[ -n "$newest" ] || { echo "no backup found in ${BACKUP_DIR}" >&2; exit 1; }
echo "restoring ${newest} into ${TEST_DB}"

cleanup() {
  docker compose exec -T "$DB_CONTAINER" psql -U "$DB_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS \"${TEST_DB}\";" > /dev/null || true
}
trap cleanup EXIT

docker compose exec -T "$DB_CONTAINER" psql -U "$DB_USER" -d postgres \
  -c "CREATE DATABASE \"${TEST_DB}\";" > /dev/null
docker compose exec -T "$DB_CONTAINER" \
  pg_restore -U "$DB_USER" -d "$TEST_DB" --no-owner --no-privileges < "$newest"

read -r tables users tracks <<< "$(docker compose exec -T "$DB_CONTAINER" psql -U "$DB_USER" \
  -d "$TEST_DB" -Atc "
    SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema='public'),
           (SELECT count(*) FROM users),
           (SELECT count(*) FROM tracks);" | tr '|' ' ')"

echo "restored: ${tables} tables, ${users} users, ${tracks} tracks"
[ "$tables" -ge 40 ] || { echo "schema looks incomplete" >&2; exit 1; }

# The restored copy must be usable, not just present: run one real query.
docker compose exec -T "$DB_CONTAINER" psql -U "$DB_USER" -d "$TEST_DB" -Atc \
  "SELECT count(*) FROM tracks t JOIN channel_tracks ct ON ct.track_id = t.id;" > /dev/null

echo "restore drill OK"
