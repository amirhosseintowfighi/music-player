"""guess the song: quiz games

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-25
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0019_quiz.sql")


def downgrade() -> None:
    run_sql("DROP TABLE IF EXISTS quiz_games;")
