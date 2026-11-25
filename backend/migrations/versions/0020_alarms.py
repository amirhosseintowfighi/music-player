"""wake-up alarms

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-25
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0020_alarms.sql")


def downgrade() -> None:
    run_sql(
        "DELETE FROM notifications WHERE kind = 'alarm';"
        "ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;"
        "ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check"
        " CHECK (kind IN ('new_tracks','digest','sub_expiry','discover_ready','payment','system',"
        "'new_release'));"
        "DROP TABLE IF EXISTS alarms;"
    )
