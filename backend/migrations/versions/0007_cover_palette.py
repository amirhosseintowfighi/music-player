"""cover palette

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-21
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0007_cover_palette.sql")


def downgrade() -> None:
    run_sql("ALTER TABLE tracks DROP COLUMN IF EXISTS cover_palette;")
