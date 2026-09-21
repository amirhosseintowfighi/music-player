"""playback infrastructure: bot_file_id rename and resolve demand

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-20
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0006_playback.sql")


def downgrade() -> None:
    run_sql(
        """
        DROP INDEX IF EXISTS tracks_resolve_demand;
        ALTER TABLE tracks DROP COLUMN IF EXISTS resolve_requests;
        ALTER TABLE tracks RENAME COLUMN bot_file_id_updated_at TO file_id_updated_at;
        ALTER TABLE tracks RENAME COLUMN bot_file_id TO file_id;
        """
    )
