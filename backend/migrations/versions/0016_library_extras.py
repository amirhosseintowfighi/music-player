"""library extras: snooze, exclude from taste profile, pins

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-28
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0016_library_extras.sql")


def downgrade() -> None:
    run_sql(
        "DROP TABLE IF EXISTS library_pins;"
        "ALTER TABLE playlists DROP COLUMN IF EXISTS exclude_from_taste;"
        # A snoozed song would otherwise stay hidden for good after the downgrade.
        "DELETE FROM hidden_tracks WHERE until IS NOT NULL;"
        "ALTER TABLE hidden_tracks DROP COLUMN IF EXISTS until;"
    )
