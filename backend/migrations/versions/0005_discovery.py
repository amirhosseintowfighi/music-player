"""channel discovery and parser health

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-20
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0005_discovery.sql")


def downgrade() -> None:
    run_sql(
        """
        DROP TABLE IF EXISTS crawl_stats_daily;
        ALTER TABLE channels DROP COLUMN IF EXISTS extraction_rate;
        DROP INDEX IF EXISTS channel_candidates_to_probe;
        ALTER TABLE channel_candidates
            DROP COLUMN IF EXISTS approved_channel_id,
            DROP COLUMN IF EXISTS mention_count,
            DROP COLUMN IF EXISTS probe_attempts,
            DROP COLUMN IF EXISTS probed_at,
            DROP COLUMN IF EXISTS subscribers;
        """
    )
