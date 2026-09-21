"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-17
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0001_initial.sql")


def downgrade() -> None:
    run_sql(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT c.relname FROM pg_class c
                     JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
                       AND NOT c.relispartition AND c.relname <> 'alembic_version'
            LOOP
                EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', r.relname);
            END LOOP;
        END $$;
        DROP FUNCTION IF EXISTS touch_updated_at() CASCADE;
        DROP FUNCTION IF EXISTS ensure_month_partitions(text, int) CASCADE;
        """
    )
