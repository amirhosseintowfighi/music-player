#!/bin/sh
# Runs once on an empty data directory (postgres image entrypoint).
# tmusic      owner / migrations (MIGRATION_DATABASE_URL)
# tmusic_app  application role behind PgBouncer: DML only, audit_log append-only
# tmusic_ro   reporting / Grafana
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE tmusic_app LOGIN PASSWORD '${APP_DB_PASSWORD}';
CREATE ROLE tmusic_ro NOLOGIN;
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO tmusic_app, tmusic_ro;
GRANT USAGE ON SCHEMA public TO tmusic_app, tmusic_ro;
-- Tables are created later by migrations as the owner; default privileges cover them.
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tmusic_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO tmusic_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
    GRANT SELECT ON TABLES TO tmusic_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO tmusic_app;
SQL
