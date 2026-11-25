"""the bot's song of the day

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-25
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0023_daily_song.sql")


def downgrade() -> None:
    run_sql(
        "DELETE FROM notifications WHERE kind = 'daily_song';"
        "ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;"
        "ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check"
        " CHECK (kind IN ('new_tracks','digest','sub_expiry','discover_ready','payment','system',"
        "'new_release','alarm'));"
    )
