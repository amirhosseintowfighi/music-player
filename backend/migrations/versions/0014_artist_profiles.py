"""artist photos and popularity

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-24
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0014_artist_profiles.sql")


def downgrade() -> None:
    run_sql(
        "DROP INDEX IF EXISTS artists_enrich_queue;"
        "ALTER TABLE artists"
        "    DROP COLUMN IF EXISTS image_url,"
        "    DROP COLUMN IF EXISTS spotify_id,"
        "    DROP COLUMN IF EXISTS popularity,"
        "    DROP COLUMN IF EXISTS enriched_at;"
    )
