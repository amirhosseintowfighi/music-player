"""growth: lyrics search, per-track loudness, channel owners

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-13
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0017_growth.sql")


def downgrade() -> None:
    run_sql(
        "DROP TABLE IF EXISTS channel_owners;"
        "ALTER TABLE tracks DROP COLUMN IF EXISTS loudness_db;"
        "ALTER TABLE tracks DROP COLUMN IF EXISTS loudness_reports;"
        "ALTER TABLE track_lyrics DROP COLUMN IF EXISTS search_text;"
    )
