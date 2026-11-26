"""listening streaks and badges

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-25
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0021_achievements.sql")


def downgrade() -> None:
    run_sql("DROP TABLE IF EXISTS user_badges;")
