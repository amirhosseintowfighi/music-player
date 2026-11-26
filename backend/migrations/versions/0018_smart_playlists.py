"""smart playlists: rules instead of hand-picked songs

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-25
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0018_smart_playlists.sql")


def downgrade() -> None:
    run_sql(
        "DELETE FROM playlists WHERE kind = 'smart';"
        "ALTER TABLE playlists DROP CONSTRAINT IF EXISTS playlists_smart_has_rules;"
        "ALTER TABLE playlists DROP COLUMN IF EXISTS refreshed_at;"
        "ALTER TABLE playlists DROP COLUMN IF EXISTS rules;"
        "ALTER TABLE playlists DROP CONSTRAINT IF EXISTS playlists_kind_check;"
        "ALTER TABLE playlists ADD CONSTRAINT playlists_kind_check"
        " CHECK (kind IN ('manual','smart_ai','discover_weekly','daily_mix','radio',"
        "'release_radar','blend','daylist'));"
    )
