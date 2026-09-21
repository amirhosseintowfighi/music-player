"""social and notifications

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-18
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0003_social_notifications.sql")


def downgrade() -> None:
    run_sql(
        """
        DROP INDEX IF EXISTS tracks_needs_probe;
        ALTER TABLE tracks DROP COLUMN IF EXISTS metadata_probed_at;
        DROP TABLE IF EXISTS wrapped_reports;
        DROP INDEX IF EXISTS notifications_due;
        DROP INDEX IF EXISTS play_history_recent;
        DROP INDEX IF EXISTS follows_followee;
        ALTER TABLE users DROP COLUMN IF EXISTS following_count;
        ALTER TABLE users DROP COLUMN IF EXISTS followers_count;
        ALTER TABLE users DROP COLUMN IF EXISTS tz_offset_minutes;
        ALTER TABLE users DROP COLUMN IF EXISTS notification_prefs;
        """
    )
