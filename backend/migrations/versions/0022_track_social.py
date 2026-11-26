"""comments and reactions on a song

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-25
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0022_track_social.sql")


def downgrade() -> None:
    run_sql("DROP TABLE IF EXISTS track_reactions; DROP TABLE IF EXISTS track_comments;")
