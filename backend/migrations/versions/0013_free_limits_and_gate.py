"""free plan limits and the forced-join gate

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-24
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0013_free_limits_and_gate.sql")


def downgrade() -> None:
    run_sql(
        "UPDATE plans SET limits ="
        " '{\"channels\": 3, \"playlists\": 5, \"daily_plays\": 60, \"download\": false}'::jsonb"
        " WHERE code = 'free';"
        "UPDATE plans SET limits = limits - 'library' WHERE code <> 'free';"
        "DELETE FROM settings WHERE key = 'required_channels';"
    )
